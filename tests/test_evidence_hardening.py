from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

from agenteval.contracts import Gate, digest
from agenteval.environment import score_state
from agenteval.metrics import extra_gate
from agenteval.tracing import Recorder, record_tool


def test_parallel_fault_ordinals_follow_start_order(monkeypatch):
    import agenteval.tracing as tracing
    recorder = Recorder()
    recorder.tool_faults = [{"tool_name": "get", "call_index": 1, "mode": "timeout", "delay_seconds": 1},
                            {"tool_name": "get", "call_index": 2, "mode": "mock", "mock_output": 42}]
    entered, release = threading.Event(), threading.Event()
    def delay(seconds):
        if seconds:
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr(tracing.time, "sleep", delay)
    def invoke(key):
        with recorder.span(key, "workflow"):
            try:
                return record_tool("get", {}, lambda: -1, key)[0]
            except TimeoutError:
                return "timeout"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, "first")
        assert entered.wait(5)
        try:
            assert pool.submit(invoke, "second").result(timeout=5) == 42
        finally:
            release.set()
        assert first.result(timeout=5) == "timeout"
    assert {c["call_id"]: c["call_index"] for c in recorder.calls} == {"first": 1, "second": 2}


def test_recursive_tool_calls_do_not_reuse_fault_ordinal():
    recorder = Recorder()
    recorder.tool_faults = [{"tool_name": "get", "call_index": 2, "mode": "mock", "mock_output": 7}]
    with recorder.span("recursive", "workflow"):
        output, _ = record_tool("get", {}, lambda: record_tool("get", {}, lambda: -1, "inner")[0], "outer")
    assert output == 7
    assert {c["call_id"]: c["call_index"] for c in recorder.calls} == {"outer": 1, "inner": 2}


def test_corrupt_or_agent_sourced_state_cannot_produce_a_score():
    assertions = {"checks": [{"path": "/ok", "expected": True}]}
    evidence = {"source": "independent_environment_adapter", "status": "observed", "state": {"ok": True}, "state_hash": digest({"ok": True})}
    assert score_state(assertions, evidence)[0:2] == ("scored", 1)
    corrupt = deepcopy(evidence); corrupt["state"]["ok"] = False
    assert score_state(assertions, corrupt)[0] == "evaluator_error"
    corrupt = deepcopy(evidence); corrupt["source"] = "agent_self_report"
    assert score_state(assertions, corrupt)[0] == "evaluator_error"


def test_pending_reset_cannot_pass_gate_despite_successful_state_score():
    report = {"scoring_coverage": 1, "specialty_metrics": {"state": {"reset_failures": 0, "reset_unconfirmed": 1}}}
    failed, missing = extra_gate(Gate().model_dump(), report, report)
    assert not failed and missing == ["ENVIRONMENT_RESET_UNCONFIRMED"]


def test_environment_configuration_is_part_of_strict_comparison(client, setup_run):
    s = setup_run
    baseline = s["post"]("evaluations", s["body"])
    definition = s["profile"]["definition"]
    profile = s["post"]("profile-versions", {"project_id": s["project"]["id"], "name": "With environment", "version": "1",
        **definition, "environment": {"adapter_id": "sqlite.refund.v1"}})
    candidate = s["post"]("evaluations", {**s["body"], "profile_version_id": profile["id"]})
    body = {"baseline_batch_id": baseline["grading_batch_id"], "candidate_batch_id": candidate["grading_batch_id"]}
    refused = client.post("/api/v1/regressions/compare", json=body)
    assert refused.status_code == 409 and "environment" in refused.json()["detail"]["differences"]
    compared = s["post"]("regressions/compare", {**body, "mode": "exploratory"})["report"]
    assert "environment" in compared["differences"] and compared["gate"]["status"] == "inconclusive"
