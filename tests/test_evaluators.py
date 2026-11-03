import math

import pytest
from pydantic import ValidationError

from agenteval.contracts import BenchmarkVersionCreate, EvaluatorSpec, ProfileCreate, digest, pointer
from agenteval.evaluators import evaluate, verdict


def spec(id="exact_match", **kwargs):
    return EvaluatorSpec(instance_id="check", id=id, **kwargs)


def test_type_sensitive_exact_match_and_null():
    assert evaluate(spec(), {"assertions": {"expected_output": True}}, {"output": 1})["passed"] is False
    assert evaluate(spec(), {"assertions": {"expected_output": None}}, {"output": None})["passed"] is True
    assert evaluate(spec(), {"assertions": {"expected_output": None}}, {})["grading_status"] == "missing_evidence"


def test_json_pointer_escaped_keys():
    assert pointer({"a/b": {"~": [42]}}, "/a~1b/~0/0") == 42


def test_no_remote_schema_references():
    with pytest.raises(ValidationError):
        spec("json_schema", config={"schema": {"$ref": "https://untrusted.test/schema"}})


def test_schema_mismatch_is_real_failure():
    actual = evaluate(spec("json_schema", config={"schema": {"type": "integer"}}), {}, {"output": "1"})
    assert actual["passed"] is False


def test_partial_trace_cannot_prove_absence_but_can_prove_forbidden_action():
    s = spec("tool_selection", hard_constraint=True, config={"forbidden": ["delete"]})
    case = {"assertions": {"tools": {"allowed": ["get"], "required": ["get"]}}}
    absent = evaluate(s, case, {"observations": {"tool_calls": []}})
    assert absent["grading_status"] == "missing_evidence" and absent["score"] is None
    observed = evaluate(s, case, {"observations": {"tool_calls": [{"name": "delete"}]}})
    assert observed["passed"] is False


def test_tool_argument_semantics():
    case = {"assertions": {"tools": {"arguments": {"get": {"id": "1001"}}}}}
    response = {"observations": {"tool_calls": [{"name": "get", "arguments": {"id": "1002"}}]}, "observations_complete": ["tool_calls"]}
    assert evaluate(spec("tool_arguments"), case, response)["passed"] is False


def test_trajectory_dependency_failure():
    case = {"assertions": {"trajectory": {"required_steps": ["lookup", "respond"], "dependencies": [["lookup", "respond"]]}}}
    response = {"observations": {"workflow_steps": ["respond", "lookup"]}, "observations_complete": ["workflow_steps"]}
    assert evaluate(spec("trajectory"), case, response)["passed"] is False


@pytest.mark.parametrize("metric,expected", [("recall", .5), ("hit", 1), ("mrr", .5), ("ndcg", (1 / math.log2(3)) / (3 + 1 / math.log2(3)))])
def test_rag_fixed_hand_calculated_examples(metric, expected):
    case = {"assertions": {"retrieval": {"relevance": {"a": 2, "b": 1, "x": 0}}}}
    response = {"observations": {"retrieval_results": ["x", "b", "b", "a"]}, "observations_complete": ["retrieval_results"]}
    item = evaluate(spec("rag", config={"metric": metric, "k": 2}, threshold=0), case, response)
    assert item["score"] == pytest.approx(expected)


@pytest.mark.parametrize("status,expected", [("timed_out", "failed"), ("agent_failed", "failed"),
                          ("infrastructure_failed", "excluded"), ("cancelled", "excluded"),
                          ("outcome_unknown", "unknown")])
def test_execution_failure_denominators(status, expected):
    assert verdict(status, [])[0] == expected


def test_required_failure_dominates_other_missing_evidence():
    failed = {"instance_id": "answer", "required": True, "passed": False, "grading_status": "scored"}
    missing = {"instance_id": "tools", "required": True, "passed": None, "grading_status": "missing_evidence"}
    assert verdict("completed", [failed, missing])[0] == "failed"
    assert verdict("completed", [missing])[0] == "unknown"


def test_hash_is_order_invariant_but_type_sensitive():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest(True) != digest(1)


def test_duplicate_case_keys_rejected():
    c = {"case_key": "x", "input": {}, "assertions": {}}
    with pytest.raises(ValidationError):
        BenchmarkVersionCreate(version="1", cases=[c, c])


def test_profiles_cannot_only_have_optional_checks():
    with pytest.raises(ValidationError):
        ProfileCreate(project_id="p", name="p", version="1", evaluators=[{"instance_id": "x", "id": "exact_match", "required": False}])

