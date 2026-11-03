from copy import deepcopy
import math

import pytest
from pydantic import ValidationError

from agenteval.calibration import analyze
from agenteval.contracts import EvaluatorSpec, Gate
from agenteval.evaluators import evaluate, validate_case
from agenteval.metrics import extra_gate


@pytest.fixture(autouse=True)
def private_environments(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTEVAL_ENVIRONMENT_ROOT", str(tmp_path / "environments"))


def tool_spec(**config):
    return EvaluatorSpec(instance_id="calls", id="tool_call", config=config)


def test_missing_tool_requires_complete_channel_and_partial_trace_is_not_complete():
    case = {"assertions": {"tool_calls": [{"call_id": "lookup", "name": "get"}]}}
    response = {"observations": {"tool_calls": []}}
    assert evaluate(tool_spec(), case, response)["grading_status"] == "missing_evidence"
    response["observations_complete"] = ["tool_calls"]
    assert evaluate(tool_spec(), case, response)["passed"] is False
    response["trace_completeness"] = "partial"
    assert evaluate(tool_spec(), case, response)["grading_status"] == "missing_evidence"
    response["observations"]["tool_calls"] = [{"call_id": "lookup", "name": "get", "status": "error"}]
    assert evaluate(tool_spec(), case, response)["passed"] is False


def test_tool_mapping_is_explicit_and_occurrence_matching_requires_complete_order():
    case = {"assertions": {"tool_calls": [{"call_id": "lookup", "name": "get", "expected_arguments": {"id": 1}}]}}
    response = {"observations_complete": ["tool_calls"], "observations": {"tool_calls": [
        {"call_id": "provider-42", "name": "get", "arguments": {"id": 1}, "output": {}, "status": "ok"}]}}
    assert evaluate(tool_spec(call_id_map={"lookup": "provider-42"}), case, response)["passed"]
    del response["observations"]["tool_calls"][0]["call_id"]
    assert evaluate(tool_spec(), case, response)["grading_status"] == "missing_evidence"
    assert evaluate(tool_spec(match_by="name_occurrence"), case, response)["passed"]
    response["observations_complete"] = []
    assert evaluate(tool_spec(match_by="name_occurrence"), case, response)["grading_status"] == "missing_evidence"
    with pytest.raises(ValidationError):
        tool_spec(call_id_map={"one": "same", "two": "same"})


def test_missing_tool_status_is_not_a_proven_failure():
    case = {"assertions": {"tool_calls": [{"call_id": "a", "name": "get"}]}}
    response = {"observations_complete": ["tool_calls"], "observations": {"tool_calls": [
        {"call_id": "a", "name": "get", "arguments": {}, "output": {}}]}}
    assert evaluate(tool_spec(), case, response)["grading_status"] == "missing_evidence"


def trajectory(rules, response):
    return evaluate(EvaluatorSpec(instance_id="path", id="trajectory"), {"assertions": {"trajectory": rules}}, response)


def test_trajectory_redundancy_forbidden_actions_and_partial_evidence():
    response = {"observations": {"workflow_steps": ["retrieve", "retrieve", "retrieve", "answer"], "tool_calls": []},
                "observations_complete": ["workflow_steps", "tool_calls"]}
    rules = {"required_steps": ["retrieve", "answer"], "max_step_repetitions": {"retrieve": 2}}
    assert trajectory(rules, response)["passed"] is False
    response["observations_complete"] = []
    assert trajectory(rules, response)["passed"] is False  # Three observed calls are a positive witness.
    response["observations"]["workflow_steps"] = ["retrieve"]
    assert trajectory(rules, response)["grading_status"] == "missing_evidence"
    response["observations"]["tool_calls"] = [{"name": "delete"}]
    assert trajectory({"forbidden_tools": ["delete"]}, response)["passed"] is False


def test_incomplete_tool_arguments_cannot_prove_identical_call_loop():
    response = {"observations": {"workflow_steps": [], "tool_calls": [{"name": "get"}, {"name": "get"}]},
                "observations_complete": ["workflow_steps", "tool_calls"]}
    assert trajectory({"max_identical_tool_calls": 1}, response)["grading_status"] == "missing_evidence"


def test_loop_metric_is_unavailable_without_a_loop_rule():
    value = trajectory({"required_steps": ["answer"]}, {"observations": {"workflow_steps": ["answer"]},
                                                       "observations_complete": ["workflow_steps"]})
    assert value["passed"] and value["evidence"]["loop_detected"] is None


def test_trajectory_alternative_paths_branches_and_transitions():
    response = {"output": {"cached": True}, "observations": {"workflow_steps": ["cache", "answer"],
        "state_transitions": [{"from": "ready", "to": "done"}]}, "observations_complete": ["workflow_steps", "state_transitions"]}
    rules = {"allowed_paths": [["cache", "answer"], ["retrieve", "answer"]],
             "branches": [{"path": "/output/cached", "equals": True, "required_steps": ["cache"], "forbidden_steps": ["retrieve"]}],
             "allowed_transitions": [["ready", "done"]]}
    assert trajectory(rules, response)["passed"]
    response["observations"]["state_transitions"][0]["to"] = "deleted"
    assert trajectory(rules, response)["passed"] is False
    response["output"] = {}
    del response["observations"]["state_transitions"]
    assert trajectory(rules, response)["grading_status"] == "missing_evidence"


def test_recovery_requires_timing_and_successful_fallback_after_failure():
    response = {"observations": {"workflow_steps": ["lookup", "fallback"], "tool_calls": [
        {"name": "get", "status": "error", "end_ns": 5}]}, "observations_complete": ["workflow_steps", "tool_calls"],
        "trace_spans": [{"name": "fallback", "kind": "workflow", "status": "ok", "start_ns": 6}]}
    rules = {"recovery": [{"tool": "get", "step": "fallback"}]}
    assert trajectory(rules, response)["passed"]
    response["trace_spans"][0]["start_ns"] = 4
    assert trajectory(rules, response)["passed"] is False
    response["trace_spans"] = []
    assert trajectory(rules, response)["grading_status"] == "missing_evidence"


def test_evaluator_metadata_and_dynamic_judge_granularity(client):
    registry = client.get("/api/v1/evaluators").json()["items"]
    assert {r["granularity"] for r in registry} == {"e2e", "trajectory", "tool", "component"}
    spec = EvaluatorSpec(instance_id="judge", id="llm_judge", config={"rubric": "Check the path", "granularity": "trajectory", "actual_path": "/observations/workflow_steps"})
    assert evaluate(spec, {}, {})["metadata"]["granularity"] == "trajectory"
    with pytest.raises(ValidationError):
        EvaluatorSpec(instance_id="judge", id="llm_judge", config={"rubric": "Check the path", "granularity": "trajectory"})


def test_state_verifier_cannot_be_spoofed_by_agent_output(client, setup_run):
    s = setup_run
    post = s["post"]
    bm = post("benchmarks", {"project_id": s["project"]["id"], "name": "Refund environment"})
    bv = post(f"benchmarks/{bm['id']}/versions", {"version": "1", "cases": [{"case_key": "refund", "input": {"order_id": "1001"},
        "assertions": {"expected_output": {"order_id": "1001", "status": "requested"},
                       "state": {"checks": [{"path": "/refunds", "expected": [{"order_id": "1001", "status": "requested"}]}]}}}]})
    profile = post("profile-versions", {"project_id": s["project"]["id"], "name": "Independent state", "version": "1",
        "environment": {"adapter_id": "sqlite.refund.v1"}, "evaluators": [
            {"instance_id": "answer", "id": "exact_match"}, {"instance_id": "state", "id": "state_assertion"}]})
    for adapter, plugin, passed in [("python", "example.refund.python", True), ("python", "example.refund.liar", False), ("langgraph", "example.refund.graph", True)]:
        agent = post("agents", {"project_id": s["project"]["id"], "name": plugin, "adapter_type": adapter})
        av = post(f"agents/{agent['id']}/versions", {"version": "1", "configuration": {"plugin_id": plugin}})
        run = post("evaluations", {"agent_version_id": av["id"], "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]})
        report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
        assert report["counts"]["passed" if passed else "failed"] == 1
        assert report["specialty_metrics"]["state"]["validation_scope"] == "independent_state"
        results = client.get(f"/api/v1/evaluations/{run['id']}/results").json()["items"]
        assert next(r for r in results if r["instance_id"] == "answer")["result"]["passed"] is True
        execution = client.get(f"/api/v1/executions/{report['trials'][0]['execution_id']}").json()
        evidence = execution["environment_evidence"]
        assert evidence["source"] == "independent_environment_adapter" and evidence["reset_status"] == "verified"
        assert evidence["state"]["refunds"] == ([{"order_id": "1001", "status": "requested"}] if passed else [])
        regrade = post(f"evaluations/{run['id']}/regrade", {})
        new = client.get(f"/api/v1/evaluations/{run['id']}/report?grading_batch_id={regrade['id']}").json()
        assert new["counts"] == report["counts"]  # Uses frozen pre-reset snapshot, not the reset live DB.


def test_agent_supplied_state_is_not_independent_evidence():
    spec = EvaluatorSpec(instance_id="state", id="state_assertion")
    case = {"assertions": {"state": {"checks": [{"path": "/ok", "expected": True}]}}}
    response = {"environment_evidence": {"state": {"ok": True}}, "observations": {"final_state": {"ok": True}}}
    assert evaluate(spec, case, response)["grading_status"] == "missing_evidence"


def test_unknown_environment_effect_is_not_reset(tmp_path):
    from agenteval.environment import RefundEnvironment, collect
    adapter = RefundEnvironment(tmp_path, "unknown")
    adapter.prepare({})
    value = collect(adapter, False)
    assert value["reset_status"] == "deferred_unknown_effect"
    with pytest.raises(FileExistsError):
        adapter.prepare({})


def test_performance_gate_requires_sample_size_and_evidence():
    base = {"scoring_coverage": 1, "latency_ms": {"p95": 500, "samples": 2}, "specialty_metrics": {
        "tool": {"samples": 3, "coverage": 1, "status_coverage": 1, "error_rate": 0}, "state": {"reset_failures": 0}}}
    gate = Gate(max_p95_latency_ms=100).model_dump()
    failed, missing = extra_gate(gate, base, base)
    assert not failed and "LATENCY_SAMPLES_INSUFFICIENT" in missing
    base["latency_ms"]["samples"] = 20
    assert extra_gate(gate, base, base)[0] == ["P95_LATENCY_THRESHOLD"]
    gate = Gate(max_tool_error_rate=0).model_dump()
    assert "TOOL_EVIDENCE_INSUFFICIENT" in extra_gate(gate, base, base)[1]


def test_partial_grading_coverage_keeps_unwritten_results_in_denominator(client, setup_run):
    from sqlalchemy import select
    from agenteval.db import EvaluationResult
    s = setup_run
    run = s["post"]("evaluations", s["body"])
    path = f"/api/v1/evaluations/{run['id']}/report"
    assert client.get(path).json()["scoring_coverage"] == 1
    with client.app.state.sessions() as session:
        values = list(session.scalars(select(EvaluationResult).filter_by(batch_id=run["grading_batch_id"])))
        count = len(values)
        session.delete(values[0]); session.commit()
    assert client.get(path).json()["scoring_coverage"] == pytest.approx((count - 1) / count)


def test_judge_calibration_counts_errors_reviews_missing_and_variance():
    labels = [{"case_key": "a", "label": "passed", "rationale": "correct"}, {"case_key": "b", "label": "failed", "rationale": "wrong"}]
    rows = [{"case_key": "a", "grading_status": "scored", "passed": True, "score": 1},
            {"case_key": "a", "grading_status": "scored", "passed": False, "score": 0},
            {"case_key": "b", "grading_status": "evaluator_error", "passed": None, "score": None}]
    value = analyze(labels, rows, planned_repetitions=2)
    assert value["planned_ratings"] == 4 and value["agreement_rate"] == .25
    assert value["rating_coverage"] == .5 and value["judge_error_rate"] == .25
    assert value["repeat_stability"][0]["score_stddev"] == .5
    assert value["repeat_stability"][0]["decision_consistent"] is False
    assert value["repeat_stability"][1]["complete"] is False


@pytest.mark.parametrize("component_id", ["sqlite.fts5", "tfidf.cosine"])
def test_independent_rag_algorithms_share_runner_and_labeled_metrics(client, setup_run, component_id):
    s = setup_run
    post = s["post"]
    bm = post("benchmarks", {"project_id": s["project"]["id"], "name": "RAG labels"})
    bv = post(f"benchmarks/{bm['id']}/versions", {"version": "1", "cases": [{"case_key": "relevance", "input": {
        "query": "refund payment", "k": 1, "documents": [{"id": "a", "text": "refund payment"}, {"id": "b", "text": "tracking parcel"}]},
        "assertions": {"retrieval": {"judgment_status": "complete", "relevance": {"a": 2, "b": 0}}}}]})
    profile = post("profile-versions", {"project_id": s["project"]["id"], "name": "RAG", "version": "1",
        "evaluators": [{"instance_id": metric, "id": "rag", "config": {"metric": metric, "k": 1}} for metric in ("recall", "mrr", "ndcg")]})
    run = post("component-evaluations", {"project_id": s["project"]["id"], "component_id": component_id,
        "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]})
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    metrics = report["specialty_metrics"]["granularities"]["component"]["evaluators"]
    assert len(metrics) == 3 and all(m["mean_score"] == 1 and m["coverage"] == 1 for m in metrics)
