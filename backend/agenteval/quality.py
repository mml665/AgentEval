"""Deterministic facts and release suggestions; no inferred cause is asserted as fact."""
from agenteval.contracts import digest, canonical


def case_identity(case):
    return digest({"input": case["input"], "assertions": case["assertions"]})


def diagnose(case, execution, results, core_keys, historical_hard=()):
    facts = [{"kind": "execution", "status": execution.status, "source": "platform_executor"}]
    for row in [*results, *historical_hard]:
        value = row.result
        facts.append({"kind": value["evaluator_id"], "instance_id": row.instance_id,
            "status": value["grading_status"], "passed": value["passed"], "score": value["score"],
            "evidence": value["evidence"], "provenance": value.get("evidence_provenance", {"source": "legacy_unclassified"})})
        facts[-1].update(source_execution_id=row.execution_id, historical=row.execution_id != execution.id)
    values = [r.result for r in results]
    failed = [v for v in values if v["passed"] is False] + [r.result for r in historical_hard]
    state_failed = any(v["evaluator_id"] == "state_assertion" for v in failed)
    response = execution.response or {}
    assertions = case.definition.get("assertions", {})
    output_passed = (any(v["evaluator_id"] == "exact_match" and v["passed"] is True for v in values)
        and "output" in response and "expected_output" in assertions
        and canonical(response["output"]) == canonical(assertions["expected_output"]))
    category = "output_state_mismatch" if state_failed and output_passed else "state_verification" if state_failed else (
        "tool_use" if any(v["evaluator_id"].startswith("tool_") for v in failed) else
        "trajectory" if any(v["evaluator_id"] == "trajectory" for v in failed) else
        "output_correctness" if failed else "execution_failure" if execution.status in ("agent_failed", "timed_out") else "evidence_gap")
    severity = "blocking" if any(v["hard_constraint"] for v in failed) or (case.case_key in core_keys and (failed or execution.status in ("agent_failed", "timed_out"))) else "warning" if failed or execution.status in ("agent_failed", "timed_out") else "needs_evidence"
    suggestions = {"output_state_mismatch": "检查业务工具是否执行、事务是否提交，以及状态探针的观察范围。",
        "state_verification": "对照环境快照与状态断言，确认业务写入链路。",
        "tool_use": "检查匹配的调用 ID、参数、结果及依赖顺序。",
        "trajectory": "对照违规规则和时间线，检查分支、循环或恢复步骤。",
        "output_correctness": "核对最终输出和参考标签，确认是否存在业务规则差异。",
        "execution_failure": "检查执行错误和失败前的 Trace；确认工具、Agent 或超时配置。",
        "evidence_gap": "补充缺失的观测或修复评分异常，再重新评测。"}
    return category, severity, {"facts": facts, "candidate_explanation": suggestions[category],
        "cause_status": "awaiting_developer_confirmation", "case_hash": case_identity(case.definition),
        "trace_completeness": execution.trace_completeness,
        "environment_evidence": execution.environment_evidence, "provenance": execution.evidence_provenance}


def release_snapshot(comparison, blockers, newer_grading, note, engine_current=True):
    report = comparison.report
    gate = report["gate"]
    reasons = list(gate["reasons"])
    evidence_complete = all(r["decision_coverage"] == 1 and r.get("trial_scoring_coverage", r["scoring_coverage"]) == 1
                            for r in (report["baseline"], report["candidate"]))
    if not engine_current or report["mode"] != "strict" or not report["compatible"]:
        status = "insufficient"
    elif gate["status"] == "failed" or blockers:
        status = "blocked"
    elif gate["status"] != "passed" or not evidence_complete or newer_grading:
        status = "insufficient"
    else:
        status = "ready"
    if blockers:
        reasons.append("UNRESOLVED_BLOCKING_ISSUES")
    if not evidence_complete:
        reasons.append("RELEASE_EVIDENCE_INCOMPLETE")
    if newer_grading:
        reasons.append("NEWER_GRADING_BATCH_EXISTS")
    if not engine_current:
        reasons.append("ENGINE_REASSESSMENT_REQUIRED")
    return {"schema_version": "1", "policy_version": "release-1.0", "status": status,
        "comparison_id": comparison.id, "comparison": report, "reasons": reasons,
        "blocking_issues": blockers, "evidence_complete": evidence_complete, "newer_grading": newer_grading,
        "engine_current": engine_current, "note": note, "scope": "对固定版本与评分批次的发布建议，不执行部署，也不替代生产审批。"}
