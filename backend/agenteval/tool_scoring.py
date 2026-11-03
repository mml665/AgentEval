from jsonschema import Draft202012Validator
from pydantic import Field, model_validator

from agenteval.contracts import Contract, EvaluatorSpec, canonical, pointer


class ToolAssertion(Contract):
    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1)
    expected_arguments: dict = {}
    arguments_schema: dict | None = None
    result_schema: dict | None = None
    expected_output: object = None
    after: list[str] = []
    usage: list[dict[str, str]] = []

    @model_validator(mode="after")
    def schemas(self):
        from agenteval.evaluators import validate_config
        for schema in (self.arguments_schema, self.result_schema):
            if schema is not None:
                # Reuse the reference restrictions; do not resolve remote schemas.
                validate_config(EvaluatorSpec.model_construct(id="json_schema", config={"schema": schema}))
        for use in self.usage:
            if set(use) != {"actual_path", "tool_output_path"} or any(not x.startswith("/") for x in use.values()):
                raise ValueError("Usage rules require JSON Pointer paths")
        return self


def validate_assertions(assertions):
    if not isinstance(assertions, list) or not assertions:
        raise ValueError("Non-empty tool_calls assertions required")
    rules = [ToolAssertion.model_validate(r) for r in assertions]
    ids = [r.call_id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate expected call ID")
    if any(dependency not in ids or dependency == r.call_id for r in rules for dependency in r.after):
        raise ValueError("Invalid tool dependency")
    graph = {r.call_id: r.after for r in rules}
    def visit(node, path):
        if node in path:
            raise ValueError("Cyclic tool dependency")
        for parent in graph[node]:
            visit(parent, path | {node})
    for node in graph:
        visit(node, set())


def score_calls(assertions, calls, response, allow_extra_calls=False, *, complete=False, call_id_map=None, match_by="call_id"):
    from copy import deepcopy
    calls = deepcopy(calls)
    if match_by == "name_occurrence":
        if not complete:
            return "missing_evidence", None, {"reason": "Occurrence mapping requires a complete ordered tool channel"}
        # Explicit opt-in: nth expected occurrence of a name maps to nth observed occurrence.
        groups = {}
        for rule in assertions:
            groups.setdefault(rule["name"], []).append(rule["call_id"])
        counts = {}
        for index, call in enumerate(calls):
            name = call.get("name")
            occurrence = counts.get(name, 0)
            counts[name] = occurrence + 1
            candidates = groups.get(name, [])
            call["call_id"] = candidates[occurrence] if occurrence < len(candidates) else f"__extra_{index}"
    elif call_id_map:
        inverse = {observed: expected for expected, observed in call_id_map.items()}
        for call in calls:
            call["call_id"] = inverse.get(call.get("call_id"), call.get("call_id"))
    ids = [c.get("call_id") for c in calls]
    if any(not x for x in ids) or len(ids) != len(set(ids)):
        return "missing_evidence", None, {"reason": "Unique call_id required for every observed call"}
    by_id = {c["call_id"]: c for c in calls}
    evidence = []
    for raw in assertions:
        rule = ToolAssertion.model_validate(raw)
        call = by_id.get(rule.call_id)
        checks = {}
        if call is None:
            checks["present"] = False if complete else None
        else:
            checks["name"] = call["name"] == rule.name if "name" in call else None
            checks["success"] = call["status"] == "ok" if call.get("status") in ("ok", "error") else None
            arguments = call.get("arguments")
            checks["arguments"] = (all(k in arguments and canonical(arguments[k]) == canonical(v)
                for k, v in rule.expected_arguments.items()) if isinstance(arguments, dict) else None)
            if rule.arguments_schema is not None:
                checks["arguments_schema"] = Draft202012Validator(rule.arguments_schema).is_valid(arguments) if isinstance(arguments, dict) else None
            if rule.result_schema is not None:
                checks["result_schema"] = Draft202012Validator(rule.result_schema).is_valid(call["output"]) if "output" in call else None
            if "expected_output" in raw:
                checks["output"] = canonical(call["output"]) == canonical(raw["expected_output"]) if "output" in call else None
            # Successful execution requires a captured result, even when no output assertion is set.
            checks["output_present"] = True if "output" in call else None
            for dependency in rule.after:
                other = by_id.get(dependency)
                checks[f"after:{dependency}"] = (other["end_ns"] <= call["start_ns"]
                    if other and "end_ns" in other and "start_ns" in call else None)
            for i, use in enumerate(rule.usage):
                try:
                    checks[f"usage:{i}"] = canonical(pointer(response, use["actual_path"])) == canonical(pointer(call["output"], use["tool_output_path"]))
                except (KeyError, IndexError, TypeError):
                    checks[f"usage:{i}"] = None
        passed = False if False in checks.values() else None if None in checks.values() else True
        evidence.append({"call_id": rule.call_id, "checks": checks, "passed": passed})
    if not allow_extra_calls:
        expected = {r["call_id"] for r in assertions}
        evidence.extend({"call_id": key, "checks": {"expected": False}, "passed": False} for key in by_id if key not in expected)
    counts = {"passed": sum(r["passed"] is True for r in evidence),
              "failed": sum(r["passed"] is False for r in evidence),
              "unknown": sum(r["passed"] is None for r in evidence)}
    details = {"calls": evidence, "counts": counts, "complete": complete, "match_by": match_by}
    # Conjunction of required call assertions: a failure cannot be averaged into a pass.
    if counts["failed"]:
        return "scored", 0.0, details
    if counts["unknown"] or not complete:
        return "missing_evidence", None, details
    return "scored", 1.0, details
