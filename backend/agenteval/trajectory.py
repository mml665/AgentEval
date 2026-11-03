"""Rule-based trajectory constraints with explicit absence/evidence semantics."""
from collections import Counter

from pydantic import Field, model_validator

from agenteval.contracts import Contract, canonical, pointer


class Branch(Contract):
    path: str = Field(pattern=r"^/")
    equals: object
    required_steps: list[str] = []
    forbidden_steps: list[str] = []


class Recovery(Contract):
    tool: str
    step: str


class TrajectoryRules(Contract):
    required_steps: list[str] = []
    dependencies: list[list[str]] = []
    forbidden_steps: list[str] = []
    forbidden_tools: list[str] = []
    max_step_repetitions: dict[str, int] = {}
    max_identical_tool_calls: int | None = Field(default=None, ge=1)
    allowed_paths: list[list[str]] = []
    branches: list[Branch] = []
    recovery: list[Recovery] = []
    allowed_transitions: list[list[str]] = []

    @model_validator(mode="after")
    def valid_rules(self):
        if any(len(x) != 2 for x in self.dependencies + self.allowed_transitions):
            raise ValueError("Dependencies and transitions require pairs")
        if any(type(x) is not int or x < 1 for x in self.max_step_repetitions.values()):
            raise ValueError("Step repetition limits must be positive integers")
        if set(self.required_steps) & set(self.forbidden_steps):
            raise ValueError("A step cannot be required and forbidden")
        return self


def score_trajectory(definition, response):
    rules = TrajectoryRules.model_validate(definition)
    observations = response.get("observations", {})
    steps = observations.get("workflow_steps", [])
    tools = observations.get("tool_calls", [])
    channels = response.get("observations_complete", [])
    trace_ok = response.get("trace_completeness") not in ("partial", "invalid")
    complete = "workflow_steps" in channels and trace_ok
    tools_complete = "tool_calls" in channels and trace_ok
    checks = {}
    def present(name, ok, observed=False, sufficient=complete):
        checks[name] = bool(ok) if sufficient or observed else None
    present("required_steps", set(rules.required_steps) <= set(steps))
    for before, after in rules.dependencies:
        # First occurrences preserve the v1 contract; alternative exact paths handle repeats.
        seen = before in steps and after in steps
        ok = seen and steps.index(before) < steps.index(after)
        present(f"dependency:{before}->{after}", ok)
    violations = sorted(set(steps) & set(rules.forbidden_steps))
    present("forbidden_steps", not violations, bool(violations))
    if rules.forbidden_tools:
        forbidden = [c.get("name") for c in tools if c.get("name") in rules.forbidden_tools]
        present("forbidden_tools", not forbidden, bool(forbidden), tools_complete)
    loops = []
    for name, maximum in rules.max_step_repetitions.items():
        exceeded = steps.count(name) > maximum
        present(f"repetitions:{name}", not exceeded, exceeded)
        if exceeded:
            loops.append(name)
    if rules.max_identical_tool_calls is not None:
        comparable = [c for c in tools if isinstance(c.get("name"), str) and isinstance(c.get("arguments"), dict)]
        counts = Counter(canonical({"name": c["name"], "arguments": c["arguments"]}) for c in comparable)
        repeated = [key for key, count in counts.items() if count > rules.max_identical_tool_calls]
        present("identical_tool_calls", not repeated, bool(repeated), tools_complete and len(comparable) == len(tools))
        loops.extend(repeated)
    if rules.allowed_paths:
        present("allowed_paths", steps in rules.allowed_paths)
    for i, branch in enumerate(rules.branches):
        try:
            if canonical(pointer(response, branch.path)) == canonical(branch.equals):
                present(f"branch:{i}:required", set(branch.required_steps) <= set(steps))
                forbidden = bool(set(branch.forbidden_steps) & set(steps))
                present(f"branch:{i}:forbidden", not forbidden, forbidden)
        except (KeyError, IndexError, TypeError, ValueError):
            checks[f"branch:{i}:condition"] = None
    recovery_checks = []
    for i, rule in enumerate(rules.recovery):
        failures = [c for c in tools if c.get("name") == rule.tool and c.get("status") == "error"]
        if not tools_complete and not failures:
            checks[f"recovery:{i}:trigger"] = None
        for j, failure in enumerate(failures):
            spans = [s for s in response.get("trace_spans", []) if s.get("kind") == "workflow" and s.get("name") == rule.step]
            if "end_ns" not in failure or not trace_ok or any(s.get("attributes", {}).get("span.open") for s in spans):
                ok = None
            elif not spans and rule.step in steps:
                ok = None  # Step names alone cannot prove recovery happened after the failed call.
            else:
                ok = any(s["start_ns"] >= failure["end_ns"] and s.get("status") == "ok" for s in spans)
                if not complete and not ok:
                    ok = None
            checks[f"recovery:{i}:{j}"] = ok
            recovery_checks.append(ok)
    if rules.allowed_transitions:
        transitions = observations.get("state_transitions")
        if transitions is None:
            checks["state_transitions"] = None
        else:
            invalid = any([x["from"], x["to"]] not in rules.allowed_transitions for x in transitions)
            present("state_transitions", not invalid, invalid,
                    "state_transitions" in channels and trace_ok)
    evidence = {"checks": checks, "actual": steps, "loop_witnesses": loops,
                "step_coverage": len(set(rules.required_steps) & set(steps)) / len(set(rules.required_steps)) if rules.required_steps and complete else None,
                "loop_detected": bool(loops) if (rules.max_step_repetitions or rules.max_identical_tool_calls is not None) and (complete or loops) else None,
                "recovery_checks": recovery_checks, "complete": complete}
    if False in checks.values():
        return "scored", 0.0, evidence
    if None in checks.values():
        return "missing_evidence", None, evidence
    return "scored", 1.0, evidence
