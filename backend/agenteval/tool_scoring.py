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
    evidence, missing = [], False
    for raw in assertions:
        rule = ToolAssertion.model_validate(raw)
        call = by_id.get(rule.call_id)
        checks = {}
        if call is None:
            checks["present"] = False if complete else None
            missing |= not complete
        else:
            checks["name"] = call["name"] == rule.name if "name" in call else None
            missing |= checks["name"] is None
            checks["success"] = None if call.get("status") not in ("ok", "error") else call["status"] == "ok"
            missing |= checks["success"] is None
            arguments = call.get("arguments")
            if not isinstance(arguments, dict) or "output" not in call:
                missing = True
            else:
                checks["arguments"] = all(k in arguments and canonical(arguments[k]) == canonical(v) for k, v in rule.expected_arguments.items())
                for field, value in (("arguments_schema", arguments), ("result_schema", call["output"])):
                    schema = getattr(rule, field)
                    if schema is not None:
                        checks[field] = Draft202012Validator(schema).is_valid(value)
                if "expected_output" in raw:
                    checks["output"] = canonical(call["output"]) == canonical(raw["expected_output"])
                for dependency in rule.after:
                    other = by_id.get(dependency)
                    if not other or "end_ns" not in other or "start_ns" not in call:
                        missing = True
                    else:
                        checks[f"after:{dependency}"] = other["end_ns"] <= call["start_ns"]
                for i, use in enumerate(rule.usage):
                    try:
                        checks[f"usage:{i}"] = canonical(pointer(response, use["actual_path"])) == canonical(pointer(call["output"], use["tool_output_path"]))
                    except (KeyError, IndexError, TypeError):
                        missing = True
        passed = False if False in checks.values() else None if missing or None in checks.values() else True
        evidence.append({"call_id": rule.call_id, "checks": checks, "passed": passed})
    if not allow_extra_calls:
        expected = {r["call_id"] for r in assertions}
        evidence.extend({"call_id": key, "checks": {"expected": False}, "passed": False} for key in by_id if key not in expected)
    # Proven failures remain failures even if another call lacks evidence.
    if any(row["passed"] is False for row in evidence):
        # Unknown companion calls do not dilute a proven failure into an accidental pass.
        return "scored", 0.0 if missing else sum(r["passed"] is True for r in evidence) / len(evidence), {"calls": evidence, "complete": complete, "match_by": match_by}
    if missing:
        return "missing_evidence", None, {"calls": evidence, "complete": complete, "match_by": match_by}
    if not complete:
        return "missing_evidence", None, {"calls": evidence, "reason": "Observation channel is incomplete"}
    return "scored", 1.0, {"calls": evidence, "complete": complete, "match_by": match_by}
