"""One preflight contract shared by preview and actual persisted execution."""
from fastapi import HTTPException
from agenteval.db import AgentVersion, Agent, BenchmarkVersion, Benchmark, ProfileVersion, TestCase
from agenteval.evaluators import metadata, validate_case
from agenteval.service import records


def prepare(session, settings, body, get):
    version = get(session, AgentVersion, body.agent_version_id)
    agent = get(session, Agent, version.agent_id)
    benchmark_version = get(session, BenchmarkVersion, body.benchmark_version_id)
    benchmark = get(session, Benchmark, benchmark_version.benchmark_id)
    profile = get(session, ProfileVersion, body.profile_version_id)
    if len({agent.project_id, benchmark.project_id, profile.project_id}) != 1:
        raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
    from agenteval.credentials import validate_reference
    from agenteval.adapters import validate_agent_config
    try: validate_agent_config(agent.adapter_type, version.configuration, settings)
    except ValueError: raise HTTPException(422, detail={"code": "INVALID_AGENT_CONFIGURATION", "errors": [{"field": "/agent/configuration", "code": "DEPLOYMENT_CONFIGURATION_UNAVAILABLE"}]}) from None
    validate_reference(session, settings, agent.project_id, version.configuration)
    cases = records(session, TestCase, benchmark_version_id=benchmark_version.id)
    return check_material(settings, agent, version, benchmark, benchmark_version, profile, cases)


def check_material(settings, agent, version, benchmark, benchmark_version, profile, cases):
    from agenteval.contracts import EvaluatorSpec
    specs = [EvaluatorSpec.model_validate(x) for x in profile.definition["evaluators"]]
    if agent.adapter_type == "component":
        from agenteval.components import COMPONENTS
        from jsonschema import Draft202012Validator
        component = COMPONENTS[version.configuration["plugin_id"]]
        for case in cases:
            if not Draft202012Validator(component["input_schema"]).is_valid(case.definition["input"]):
                raise HTTPException(422, detail={"code": "COMPONENT_INPUT_INVALID", "case_key": case.case_key})
    core = set(profile.definition["gate"]["core_case_keys"])
    if core - {c.case_key for c in cases}:
        raise HTTPException(422, detail={"code": "UNKNOWN_CORE_CASE"})
    errors = []
    for spec in specs:
        for capability in metadata(spec)["required_capabilities"]:
            if capability not in version.capabilities:
                errors.append({"evaluator": spec.instance_id, "code": "CAPABILITY_MISSING", "capability": capability, "field": "/agent/capabilities"})
        for index, case in enumerate(cases):
            try:
                validate_case(spec, case.definition)
            except (KeyError, ValueError, TypeError, IndexError):
                errors.append({"case_key": case.case_key, "evaluator": spec.instance_id, "code": "INVALID_ASSERTIONS", "field": f"/cases/{index}/assertions"})
    if errors:
        raise HTTPException(422, detail={"code": "PREFLIGHT_FAILED", "errors": errors[:100]})
    if profile.definition["execution"].get("tool_faults") and agent.adapter_type not in ("python", "langgraph"):
        raise HTTPException(422, detail={"code": "FAULT_INJECTION_REQUIRES_CONTROLLED_TOOL_BOUNDARY"})
    if profile.definition.get("environment"):
        if version.effects == "read_only" or profile.definition["execution"].get("max_retries", 0):
            raise HTTPException(422, detail={"code": "STATEFUL_ENVIRONMENT_REQUIRES_NO_RETRY"})
        if any(c.definition["assertions"].get("environment_initial", {}) != {} for c in cases):
            raise HTTPException(422, detail={"code": "INVALID_ENVIRONMENT_INITIAL_STATE"})
    if profile.definition["execution"].get("max_retries", 0) and version.effects != "read_only":
        raise HTTPException(422, detail={"code": "RETRY_REQUIRES_READ_ONLY"})
    judge_specs = [e for e in specs if e.id == "llm_judge"]
    if judge_specs and (not settings.judge_endpoint or not settings.judge_model):
        raise HTTPException(422, detail={"code": "JUDGE_NOT_CONFIGURED"})
    if len(judge_specs) * len(cases) * profile.definition["execution"]["repetitions"] > settings.judge_max_calls:
        raise HTTPException(422, detail={"code": "JUDGE_BUDGET_EXCEEDED"})
    return {"project_id": agent.project_id, "agent_version_id": version.id, "agent_label": f"{agent.name} · v{version.version}",
        "benchmark_version_id": benchmark_version.id, "benchmark_label": f"{benchmark.name} · v{benchmark_version.version}",
        "profile_version_id": profile.id, "profile_label": f"{profile.name} · v{profile.version}",
        "case_count": len(cases), "planned_trials": len(cases) * profile.definition["execution"]["repetitions"],
        "execution": profile.definition["execution"], "judge": {"initial_calls": len(judge_specs) * len(cases) * profile.definition["execution"]["repetitions"],
            "possible_calls_with_retry": min(settings.judge_max_calls, 2 * len(judge_specs) * len(cases) * profile.definition["execution"]["repetitions"]), "budget": settings.judge_max_calls},
        "gate": profile.definition["gate"], "environment": profile.definition.get("environment"),
        "scope": "Fixed resource versions; preview never invokes an Agent or creates resources."}
