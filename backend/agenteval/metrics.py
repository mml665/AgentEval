"""Evidence-aware aggregation; absent metrics stay null rather than becoming zeros."""
from collections import defaultdict


def specialty_metrics(specs, selected_results, executions, all_executions, trials, rows):
    from agenteval.contracts import EvaluatorSpec
    from agenteval.evaluators import metadata
    by_instance = defaultdict(list)
    for value in selected_results:
        by_instance[value["instance_id"]].append(value)
    n = len(trials)
    evaluators = []
    for raw in specs:
        spec = EvaluatorSpec.model_validate(raw)
        values = by_instance[spec.instance_id]
        scored = [v for v in values if v["grading_status"] == "scored"]
        evaluators.append({"instance_id": spec.instance_id, "evaluator_id": spec.id, "metadata": metadata(spec),
            "config": spec.config, "expected": n, "scored": len(scored), "coverage": len(scored) / n if n else 0,
            "mean_score": sum(v["score"] for v in scored) / len(scored) if scored else None,
            "pass_rate": sum(v["passed"] is True for v in scored) / len(scored) if scored else None})
    grains = {}
    for grain in ("e2e", "trajectory", "tool", "component"):
        items = [e for e in evaluators if e["metadata"]["granularity"] == grain]
        scored = sum(e["scored"] for e in items)
        expected = sum(e["expected"] for e in items)
        # Do not average unlike metric scales into a synthetic quality score.
        grains[grain] = {"evaluators": items, "scored": scored, "expected": expected,
                         "coverage": scored / expected if expected else None}
    state = [v for v in selected_results if v["evaluator_id"] == "state_assertion" and v["grading_status"] == "scored"]
    state_coverage = len({v["execution_id"] for v in state}) / n if n else 0
    tool_executions = [e for e in executions if e.response and "tool_calls" in e.response.get("observations_complete", [])
                       and e.trace_completeness not in ("partial", "invalid")]
    calls = [c for e in tool_executions for c in e.response.get("observations", {}).get("tool_calls", [])]
    valid_calls = [c for c in calls if c.get("status") in ("ok", "error")]
    errors = sum(c["status"] == "error" for c in valid_calls)
    trajectory = [v["evidence"] for v in selected_results if v["evaluator_id"] == "trajectory"]
    loops = [e["loop_detected"] for e in trajectory if e.get("loop_detected") is not None]
    recoveries = [check for e in trajectory for check in e.get("recovery_checks", []) if check is not None]
    judge = [v for v in selected_results if v["evaluator_id"] == "llm_judge"]
    by_case = defaultdict(list)
    for row in rows:
        by_case[row["case_key"]].append(row["verdict"])
    from collections import Counter
    planned_by_case = Counter(t.case_id for t in trials)
    repetitions = max(planned_by_case.values(), default=1)
    repeated_planned = sum(count > 1 for count in planned_by_case.values())
    repeated = [v for v in by_case.values() if len(v) == repetitions and repetitions > 1 and all(x in ("passed", "failed") for x in v)]
    totals = []
    for e in executions:
        usage = (e.response or {}).get("usage", {})
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        if type(tokens) is int and tokens >= 0:
            totals.append(tokens)
    state_by_execution = defaultdict(list)
    for value in state:
        state_by_execution[value["execution_id"]].append(value["passed"])
    return {"granularities": grains, "state": {"success_rate": sum(all(v) for v in state_by_execution.values()) / len(state_by_execution) if state_by_execution else None,
        "coverage": state_coverage, "scored": len(state),
        "validation_scope": "independent_state" if n and state_coverage == 1 else "partial_state" if any(e["evaluator_id"] == "state_assertion" for e in evaluators) else "output_only",
        "reset_failures": sum((e.environment_evidence or {}).get("reset_status") == "failed" for e in all_executions),
        "reset_unconfirmed": sum(bool(e.environment_evidence) and e.environment_evidence.get("reset_status") != "verified" for e in all_executions)},
        "tool": {"error_rate": errors / len(valid_calls) if valid_calls else None, "samples": len(valid_calls),
            "coverage": len(tool_executions) / n if n else 0, "status_coverage": len(valid_calls) / len(calls) if calls else None},
        "trajectory": {"loop_rate": sum(loops) / len(loops) if loops else None, "loop_samples": len(loops),
            "recovery_rate": sum(recoveries) / len(recoveries) if recoveries else None, "recovery_samples": len(recoveries)},
        "reliability": {"timeout_rate": sum(e.status == "timed_out" for e in all_executions) / len(all_executions) if all_executions else None,
            "attempt_timeout_rate": sum(e.status == "timed_out" for e in all_executions) / len(all_executions) if all_executions else None,
            "trial_timeout_rate": sum(e.status == "timed_out" for e in executions) / n if n else None,
            "repeated_run_success": sum(all(x == "passed" for x in v) for v in repeated) / len(repeated) if repeated else None,
            "repeated_case_planned": repeated_planned,
            "repeated_case_coverage": len(repeated) / repeated_planned if repeated_planned else None,
            "repeated_case_samples": len(repeated)},
        "usage": {"agent_tokens_per_task": sum(totals) / len(totals) if totals else None,
            "agent_token_coverage": len(totals) / n if n else 0, "cost_per_success": None,
            "cost_status": "unavailable_without_verified_price_and_complete_usage"},
        "judge": {"review_rate": sum(v["grading_status"] == "pending_review" for v in judge) / len(judge) if judge else None,
            "failure_rate": sum(v["grading_status"] == "evaluator_error" for v in judge) / len(judge) if judge else None, "samples": len(judge)}}


def extra_gate(gate, baseline, candidate):
    failed, missing = [], []
    if min(baseline.get("trial_scoring_coverage", baseline["scoring_coverage"]), candidate.get("trial_scoring_coverage", candidate["scoring_coverage"])) < gate.get("min_scoring_coverage", 0):
        missing.append("SCORING_COVERAGE_INSUFFICIENT")
    maximum = gate.get("max_p95_latency_ms")
    if maximum is not None:
        latency = candidate["latency_ms"]
        if latency["samples"] < gate.get("min_latency_samples", 20) or latency["p95"] is None:
            missing.append("LATENCY_SAMPLES_INSUFFICIENT")
        elif latency["p95"] > maximum:
            failed.append("P95_LATENCY_THRESHOLD")
    maximum = gate.get("max_tool_error_rate")
    if maximum is not None:
        tool = candidate["specialty_metrics"]["tool"]
        if tool["samples"] < gate.get("min_tool_samples", 20) or tool["coverage"] < 1 or tool["status_coverage"] != 1:
            missing.append("TOOL_EVIDENCE_INSUFFICIENT")
        elif tool["error_rate"] > maximum:
            failed.append("TOOL_ERROR_RATE_THRESHOLD")
    if candidate["specialty_metrics"]["state"]["reset_failures"]:
        missing.append("ENVIRONMENT_RESET_FAILED")
    if candidate["specialty_metrics"]["state"].get("reset_unconfirmed", 0):
        missing.append("ENVIRONMENT_RESET_UNCONFIRMED")
    return failed, missing
