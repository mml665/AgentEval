"""Deterministic evaluators; missing evidence never becomes a zero score."""
import math
from typing import Any

from jsonschema import Draft202012Validator

from agenteval.contracts import EvaluatorSpec, canonical, pointer

CAPABILITIES = {
    "exact_match": None, "json_schema": None, "state_assertion": None, "tool_selection": "tool_calls",
    "tool_arguments": "tool_calls", "tool_call": "tool_calls", "trajectory": "workflow_steps", "rag": "retrieval_results", "llm_judge": None,
}


def metadata(spec):
    from agenteval.contracts import EvaluatorMetadata
    grain = {"trajectory": "trajectory", "tool_call": "tool", "tool_arguments": "tool",
             "tool_selection": "tool", "rag": "component"}.get(spec.id, "e2e")
    evidence = {"state_assertion": ["independent_environment_snapshot"], "trajectory": ["workflow_steps"],
                "tool_call": ["tool_calls"], "tool_arguments": ["tool_calls"], "tool_selection": ["tool_calls"],
                "rag": ["retrieval_results", "complete_relevance_labels"]}.get(spec.id, ["output"])
    if spec.id == "llm_judge":
        grain = spec.config.get("granularity", "e2e")
        evidence = [spec.config.get("actual_path", "/output"), "rubric"]
    capability = CAPABILITIES[spec.id]
    if spec.id == "llm_judge":
        capability = {"trajectory": "workflow_steps", "tool": "tool_calls", "component": "retrieval_results"}.get(grain)
    return EvaluatorMetadata(evaluator_id=spec.id, granularity=grain,
        quality_dimension=spec.config.get("quality_dimension", "retrieval_quality" if spec.id == "rag" else "correctness"),
        scoring_method="llm" if spec.id == "llm_judge" else "metric" if spec.id == "rag" else "rule",
        required_capabilities=[capability] if capability else [],
        required_evidence=evidence).model_dump()


def validate_config(spec):
    allowed = {
        "exact_match": {"actual_path", "expected_path"},
        "json_schema": {"actual_path", "schema"},
        "state_assertion": set(),
        "tool_selection": {"forbidden"}, "tool_arguments": set(), "tool_call": {"allow_extra_calls", "call_id_map", "match_by"},
        "trajectory": set(), "rag": {"metric", "k"}, "llm_judge": {"rubric", "timeout_seconds", "granularity", "quality_dimension", "actual_path", "expected_path"},
    }[spec.id]
    if set(spec.config) - allowed:
        raise ValueError(f"Unknown configuration keys for {spec.id}")
    if spec.id == "tool_call" and type(spec.config.get("allow_extra_calls", False)) is not bool:
        raise ValueError("allow_extra_calls must be boolean")
    if spec.id == "tool_call":
        mapping = spec.config.get("call_id_map", {})
        if not isinstance(mapping, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in mapping.items()) or len(set(mapping.values())) != len(mapping):
            raise ValueError("call_id_map must be a one-to-one expected-to-observed ID mapping")
        if spec.config.get("match_by", "call_id") not in ("call_id", "name_occurrence"):
            raise ValueError("Unknown tool matching mode")
        if mapping and spec.config.get("match_by") == "name_occurrence":
            raise ValueError("Use one explicit tool matching policy")
    if spec.id == "llm_judge":
        if spec.config.get("granularity", "e2e") not in ("e2e", "trajectory", "tool", "component"):
            raise ValueError("Unknown Judge granularity")
        if "quality_dimension" in spec.config and (not isinstance(spec.config["quality_dimension"], str) or not 1 <= len(spec.config["quality_dimension"]) <= 64):
            raise ValueError("quality_dimension must contain 1-64 characters")
        grain = spec.config.get("granularity", "e2e")
        required_path = {"trajectory": "/observations/workflow_steps", "tool": "/observations/tool_calls", "component": "/observations/retrieval_results"}.get(grain)
        if required_path and spec.config.get("actual_path") != required_path:
            raise ValueError("Non-E2E Judge must explicitly select its observation channel")
        rubric = spec.config.get("rubric")
        if not isinstance(rubric, str) or not 1 <= len(rubric) <= 4000:
            raise ValueError("Judge requires a rubric of 1-4000 characters")
        timeout = spec.config.get("timeout_seconds", 15)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("Judge timeout must be between 0 and 60 seconds")
    for name in ("actual_path", "expected_path"):
        if name in spec.config and (not isinstance(spec.config[name], str) or
                                   not spec.config[name].startswith("/")):
            raise ValueError(f"{name} must be a JSON Pointer")
    if spec.id == "json_schema":
        schema = spec.config.get("schema")
        if not isinstance(schema, dict):
            raise ValueError("json_schema requires schema")
        def check_refs(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("$ref", "$dynamicRef") and not str(item).startswith("#"):
                        raise ValueError("Remote schema references are disabled")
                    check_refs(item)
            elif isinstance(value, list):
                for item in value:
                    check_refs(item)
        check_refs(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise ValueError("Invalid JSON Schema") from exc
    if spec.id == "tool_selection":
        forbidden = spec.config.get("forbidden", [])
        if not isinstance(forbidden, list) or any(not isinstance(x, str) for x in forbidden):
            raise ValueError("forbidden must be a list of tool names")
    if spec.id == "rag":
        if spec.config.get("metric", "recall") not in ("recall", "hit", "mrr", "ndcg"):
            raise ValueError("Unsupported RAG metric")
        k = spec.config.get("k", 5)
        if type(k) is not int or not 1 <= k <= 1000:
            raise ValueError("k must be an integer between 1 and 1000")


def validate_case(spec: EvaluatorSpec, case: dict):
    """Preflight all required labels instead of failing after invoking an agent."""
    assertions = case["assertions"]
    if spec.id == "exact_match":
        pointer(case, spec.config.get("expected_path", "/assertions/expected_output"))
    elif spec.id in ("tool_selection", "tool_arguments"):
        tools = assertions["tools"]
        if spec.id == "tool_selection":
            for field in ("allowed", "required"):
                if not isinstance(tools.get(field), list) or any(not isinstance(x, str) for x in tools[field]):
                    raise ValueError(f"tools.{field} must be a list of strings")
        elif not isinstance(tools.get("arguments"), dict) or not tools["arguments"]:
            raise ValueError("Non-empty tools.arguments is required")
        elif any(not isinstance(v, dict) for v in tools["arguments"].values()):
            raise ValueError("Expected tool arguments must be objects")
    elif spec.id == "tool_call":
        from agenteval.tool_scoring import validate_assertions
        validate_assertions(assertions["tool_calls"])
        if set(spec.config.get("call_id_map", {})) - {r["call_id"] for r in assertions["tool_calls"]}:
            raise ValueError("call_id_map references unknown expected call IDs")
    elif spec.id == "trajectory":
        from agenteval.trajectory import TrajectoryRules
        TrajectoryRules.model_validate(assertions["trajectory"])
    elif spec.id == "state_assertion":
        from agenteval.environment import StateAssertions
        StateAssertions.model_validate(assertions["state"])
    elif spec.id == "rag":
        retrieval = assertions["retrieval"]
        if retrieval.get("judgment_status") != "complete":
            raise ValueError("RAG requires complete relevance labels")
        relevance = retrieval["relevance"]
        if not isinstance(relevance, dict) or not relevance:
            raise ValueError("relevance must be a non-empty object")
        if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 10
               for x in relevance.values()):
            raise ValueError("Relevance values must be finite and between 0 and 10")
        if not any(x > 0 for x in relevance.values()):
            raise ValueError("Build 0.1 requires at least one relevant document")


def result(spec, status, score=None, evidence=None, explanation=""):
    return {
        "evaluator_id": spec.id, "evaluator_version": spec.version,
        "instance_id": spec.instance_id, "grading_status": status,
        "score": score, "passed": score >= spec.threshold if status == "scored" else None,
        "required": spec.required, "hard_constraint": spec.hard_constraint,
        "explanation": explanation, "evidence": evidence or {},
        "metadata": metadata(spec),
    }


def evaluate(spec: EvaluatorSpec, case: dict, response: dict, *, environment_evidence=None) -> dict:
    try:
        if spec.id == "state_assertion":
            from agenteval.environment import score_state
            status, score, evidence = score_state(case["assertions"]["state"], environment_evidence)
            return result(spec, status, score, evidence)
        return _evaluate(spec, case, response)
    except (KeyError, IndexError):
        return result(spec, "missing_evidence", explanation="Required observable input is absent")
    except Exception:
        return result(spec, "evaluator_error", explanation="Evaluator could not process supplied evidence")


def _evaluate(spec, case, response):
    if spec.id == "llm_judge":
        return result(spec, "missing_evidence", explanation="Agent execution did not produce a gradable result")
    assertions = case.get("assertions", {})
    if spec.id == "trajectory":
        from agenteval.trajectory import score_trajectory
        status, score, evidence = score_trajectory(assertions["trajectory"], response)
        return result(spec, status, score, evidence)
    if spec.id == "tool_call":
        from agenteval.tool_scoring import score_calls
        complete = ("tool_calls" in response.get("observations_complete", []) and
                    response.get("trace_completeness") not in ("partial", "invalid"))
        status, score, evidence = score_calls(assertions["tool_calls"], response.get("observations", {}).get("tool_calls", []), response,
            spec.config.get("allow_extra_calls", False), complete=complete,
            call_id_map=spec.config.get("call_id_map"), match_by=spec.config.get("match_by", "call_id"))
        return result(spec, status, score, evidence)
    capability = CAPABILITIES[spec.id]
    if capability:
        observations = response["observations"]
        actual = observations[capability]
        # A seen forbidden action is proof even when collection is incomplete.
        if spec.id == "tool_selection":
            forbidden = set(spec.config.get("forbidden", []))
            violations = [c["name"] for c in actual if c["name"] in forbidden]
            if violations:
                return result(spec, "scored", 0.0, {"forbidden_calls": violations})
        if capability not in response.get("observations_complete", []):
            return result(spec, "missing_evidence", explanation="Observation channel is incomplete")
    if spec.id == "exact_match":
        actual = pointer(response, spec.config.get("actual_path", "/output"))
        expected = pointer(case, spec.config.get("expected_path", "/assertions/expected_output"))
        return result(spec, "scored", float(canonical(actual) == canonical(expected)),
                      {"actual": actual, "expected": expected})
    if spec.id == "json_schema":
        actual = pointer(response, spec.config.get("actual_path", "/output"))
        errors = [e.message for e in Draft202012Validator(spec.config["schema"]).iter_errors(actual)]
        return result(spec, "scored", float(not errors), {"errors": errors[:20]})
    if spec.id == "tool_selection":
        names = [x["name"] for x in actual]
        definition = assertions["tools"]
        missing = sorted(set(definition["required"]) - set(names))
        disallowed = sorted(set(names) - set(definition["allowed"]))
        return result(spec, "scored", float(not missing and not disallowed),
                      {"actual": names, "missing": missing, "disallowed": disallowed})
    if spec.id == "tool_arguments":
        expected = assertions["tools"]["arguments"]
        checks = []
        for name, fields in expected.items():
            calls = [x for x in actual if x["name"] == name]
            checks.append(bool(calls) and all(all(key in c["arguments"] and
                          canonical(c["arguments"][key]) == canonical(value)
                          for key, value in fields.items()) for c in calls))
        return result(spec, "scored", sum(checks) / len(checks),
                      {"expected": expected, "actual": actual})
    if spec.id == "rag":
        labels = assertions["retrieval"]["relevance"]
        ranked = list(dict.fromkeys(actual))[:spec.config.get("k", 5)]
        relevant = {key for key, rel in labels.items() if rel > 0}
        hits = [i + 1 for i, key in enumerate(ranked) if key in relevant]
        metric = spec.config.get("metric", "recall")
        if metric == "recall":
            score = len(hits) / len(relevant)
        elif metric == "hit":
            score = float(bool(hits))
        elif metric == "mrr":
            score = 1 / hits[0] if hits else 0
        else:
            def dcg(values):
                return sum((2 ** rel - 1) / math.log2(i + 2) for i, rel in enumerate(values))
            ideal = dcg(sorted(labels.values(), reverse=True)[:spec.config.get("k", 5)])
            score = dcg([labels.get(key, 0) for key in ranked]) / ideal
        return result(spec, "scored", score, {"metric": metric, "ranked": ranked, "relevance": labels})
    raise ValueError("Unknown evaluator")


def verdict(execution_status: str, results: list[dict]) -> tuple[str, list[str]]:
    failed = [r["instance_id"] for r in results if r["required"] and r["passed"] is False]
    if failed:
        return "failed", failed
    if execution_status in ("agent_failed", "timed_out"):
        return "failed", [execution_status]
    if execution_status in ("infrastructure_failed", "cancelled"):
        return "excluded", [execution_status]
    if execution_status != "completed":
        return "unknown", [execution_status]
    missing = [r["instance_id"] for r in results if r["required"] and r["grading_status"] != "scored"]
    return ("unknown", missing) if missing else ("passed", [])

