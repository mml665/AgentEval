import asyncio
import csv
import io

import pytest
from sqlalchemy import event

from agenteval.health import LoopHealth, resilient_tick
from agenteval.tracing import validated
from agenteval.contracts import EvaluatorSpec
from agenteval.evaluators import evaluate


async def test_maintenance_recovers_after_transient_failure(client):
    state = LoopHealth("test-maintenance")
    attempts = []
    async def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("credential-bearing messages must not be recorded")
    await resilient_tick(state, operation, client.app.state.sessions)
    assert state.state == "degraded" and state.error_code == "ConnectionError"
    await resilient_tick(state, operation, client.app.state.sessions)
    assert state.state == "healthy" and state.failures == 0
    assert client.get("/ready").status_code == 200
    assert client.get("/api/v1/operations/health").json()["maintenance"]["alive"]


def test_workspace_pagination_is_scoped_and_batch_query_count_is_bounded(client, setup_run):
    s = setup_run
    for index in range(27):
        s["post"]("agents", {"project_id": s["project"]["id"], "name": f"Agent-{index}", "adapter_type": "python"})
    queries = []
    def count(*args):
        queries.append(1)
    event.listen(client.app.state.engine, "before_cursor_execute", count)
    try:
        one = client.get(f"/api/v1/workspace/{s['project']['id']}?limit=10").json()
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", count)
    two = client.get(f"/api/v1/workspace/{s['project']['id']}?limit=10&agents_offset=10").json()
    assert one["totals"]["agents"] == 28 and len(one["agents"]) == 10
    assert not ({x["id"] for x in one["agents"]} & {x["id"] for x in two["agents"]})
    assert len(queries) <= 25


def test_trace_tree_timing_events_and_fixed_batch_exports(client, setup_run):
    s = setup_run
    run = s["post"]("evaluations", s["body"])
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    execution_id = report["trials"][0]["execution_id"]
    trace = client.get(f"/api/v1/executions/{execution_id}/trace").json()
    assert trace["completeness"] == "instrumented"
    spans = validated(trace["spans"], trace["trace_id"])
    assert {s["kind"] for s in spans} >= {"execution", "agent", "workflow", "tool"}
    root = next(s for s in spans if s["parent_span_id"] is None)
    assert root["duration_ms"] > 0
    tool = next(s for s in spans if s["kind"] == "tool")
    assert tool["parent_span_id"] != root["span_id"]
    assert any(e["name"] == "state.update" for s in spans for e in s["events"])
    base = f"/api/v1/evaluations/{run['id']}/export?grading_batch_id={run['grading_batch_id']}"
    a, b = client.get(base), client.get(base)
    assert a.status_code == 200 and a.json()["content_hash"] == b.json()["content_hash"]
    assert a.json()["report"]["grading_batch_id"] == run["grading_batch_id"]
    assert client.get(base + "&format=csv").text.startswith("\ufeffcase_key")
    assert 'attachment;' in client.get(base + "&format=html").headers["content-disposition"]
    assert client.get(base + "&format=html").text.startswith("<!doctype html>")
    assert client.get(f"/api/v1/evaluations/{run['id']}/export").status_code == 422


def test_call_id_scoring_handles_same_tool_with_different_arguments_and_usage():
    spec = EvaluatorSpec(instance_id="calls", id="tool_call")
    case = {"assertions": {"tool_calls": [
        {"call_id": "first", "name": "get", "expected_arguments": {"id": "1"}, "arguments_schema": {"type": "object", "required": ["id"]}, "expected_output": {"value": 7}},
        {"call_id": "second", "name": "get", "expected_arguments": {"id": "2"}, "after": ["first"],
         "usage": [{"actual_path": "/output/answer", "tool_output_path": "/value"}]}]}}
    response = {"output": {"answer": 8}, "observations_complete": ["tool_calls"], "observations": {"tool_calls": [
        {"call_id": "first", "name": "get", "arguments": {"id": "1"}, "output": {"value": 7}, "status": "ok", "start_ns": 1, "end_ns": 2},
        {"call_id": "second", "name": "get", "arguments": {"id": "2"}, "output": {"value": 8}, "status": "ok", "start_ns": 3, "end_ns": 4}]}}
    result = evaluate(spec, case, response)
    assert result["passed"] and len(result["evidence"]["calls"]) == 2
    response["observations"]["tool_calls"][1]["arguments"]["id"] = "1"
    assert evaluate(spec, case, response)["passed"] is False
    response["observations"]["tool_calls"][1]["call_id"] = "first"
    assert evaluate(spec, case, response)["grading_status"] == "missing_evidence"


async def test_black_box_trace_does_not_fabricate_internal_spans(monkeypatch):
    from agenteval import adapters
    from agenteval.tracing import invoke_traced
    from agenteval.config import Settings
    async def black_box(*args):
        return {"output": "answer"}
    monkeypatch.setattr(adapters, "invoke", black_box)
    capture = {}
    await invoke_traced("http", {}, {"input": {}}, 1, Settings(), capture)
    assert capture["completeness"] == "wrapper_only" and len(capture["spans"]) == 1
    assert capture["spans"][0]["kind"] == "execution"


def test_trace_rejects_cycles_orphans_and_duplicate_ids():
    from copy import deepcopy
    root = {"span_id": "1" * 16, "parent_span_id": None, "trace_id": "a" * 32, "start_ns": 1, "end_ns": 4}
    child = {"span_id": "2" * 16, "parent_span_id": "1" * 16, "trace_id": "a" * 32, "start_ns": 2, "end_ns": 3}
    assert len(validated([root, child], "a" * 32)) == 2
    with pytest.raises(ValueError):
        validated([root, root], "a" * 32)
    broken = deepcopy(child); broken["parent_span_id"] = "3" * 16
    with pytest.raises(ValueError):
        validated([root, broken], "a" * 32)
    cycle = deepcopy(root); cycle["parent_span_id"] = child["span_id"]
    with pytest.raises(ValueError):
        validated([cycle, child], "a" * 32)


def test_failed_plugin_preserves_tool_error_trace(client, setup_run):
    s = setup_run
    version = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "failing", "configuration": {"plugin_id": "example.failing"},
        "capabilities": ["tool_calls", "workflow_steps", "retrieval_results"]})
    run = s["post"]("evaluations", {**s["body"], "agent_version_id": version["id"]})
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["counts"]["failed"] == 2
    trace = client.get(f"/api/v1/executions/{report['trials'][0]['execution_id']}/trace").json()
    assert any(s["name"] == "broken_tool" and s["status"] == "error" for s in trace["spans"])


def test_timeout_keeps_open_child_span_and_marks_partial(client, setup_run):
    s = setup_run
    version = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "trace-timeout", "configuration": {"plugin_id": "example.slow"},
        "capabilities": ["tool_calls", "workflow_steps", "retrieval_results"]})
    profile = s["post"]("profile-versions", {"project_id": s["project"]["id"], "name": "Trace timeout", "version": "1",
        "evaluators": [{"instance_id": "answer", "id": "exact_match"}], "execution": {"timeout_seconds": 1.5}})
    run = s["post"]("evaluations", {**s["body"], "agent_version_id": version["id"], "profile_version_id": profile["id"]})
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    trace = client.get(f"/api/v1/executions/{report['trials'][0]['execution_id']}/trace").json()
    assert trace["completeness"] == "partial"
    assert any(s["attributes"].get("span.open") for s in trace["spans"])
    assert any(s["kind"] == "execution" and s["status"] == "error" for s in trace["spans"])


@pytest.mark.parametrize("component_id", ["example.retriever", "example.reranker"])
def test_independent_retriever_run_and_safe_csv_export(client, setup_run, component_id):
    s = setup_run
    benchmark = s["post"]("benchmarks", {"project_id": s["project"]["id"], "name": "Component cases"})
    version = s["post"](f"benchmarks/{benchmark['id']}/versions", {"version": "1", "cases": [{
        "case_key": "=2+2", "input": {"query": "shipped order", "k": 1, "documents": [
            {"id": "policy-refund", "text": "refund payment"}, {"id": "policy-shipped", "text": "shipped order"}]},
        "assertions": {"retrieval": {"judgment_status": "complete", "relevance": {"policy-shipped": 1, "policy-refund": 0}}}}]})
    profile = s["post"]("profile-versions", {"project_id": s["project"]["id"], "name": "Recall", "version": "1",
        "evaluators": [{"instance_id": "recall", "id": "rag", "config": {"metric": "recall", "k": 1}}]})
    run = s["post"]("component-evaluations", {"project_id": s["project"]["id"], "component_id": component_id,
        "benchmark_version_id": version["id"], "profile_version_id": profile["id"]})
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["success_rate"] == 1
    trace = client.get(f"/api/v1/executions/{report['trials'][0]['execution_id']}/trace").json()
    assert any(s["kind"] == "component" for s in trace["spans"])
    csv_text = client.get(f"/api/v1/evaluations/{run['id']}/export?grading_batch_id={run['grading_batch_id']}&format=csv").text
    row = list(csv.reader(io.StringIO(csv_text)))[1]
    assert row[0] == "'=2+2"
    assert client.get("/api/v1/components").json()["items"][0]["input_schema"]
