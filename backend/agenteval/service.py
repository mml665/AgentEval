import platform
import sys
from collections import Counter

from sqlalchemy import select

from agenteval import __version__
from agenteval.contracts import nearest_rank
from agenteval.db import (Agent, AgentVersion, BenchmarkVersion, EvaluationResult, Execution,
                         GradingBatch, ProfileVersion, Run, TestCase, Trial, TrialAssessment)


def records(session, model, **filters):
    return list(session.scalars(select(model).filter_by(**filters).order_by(model.created_at, model.id)))


def snapshot(session, run, settings=None):
    from agenteval.config import Settings
    settings = settings or Settings()
    agent_version = session.get(AgentVersion, run.agent_version_id)
    agent = session.get(Agent, agent_version.agent_id)
    benchmark = session.get(BenchmarkVersion, run.benchmark_version_id)
    profile = session.get(ProfileVersion, run.profile_version_id)
    return {
        "schema_version": "0.1", "engine_version": __version__,
        "agent": {"version_id": agent_version.id, "adapter_type": agent.adapter_type,
                  "configuration": agent_version.configuration, "capabilities": agent_version.capabilities,
                  "content_hash": agent_version.content_hash, "effects": agent_version.effects},
        "benchmark": {"version_id": benchmark.id, "content_hash": benchmark.content_hash},
        "profile": profile.definition, "profile_hash": profile.content_hash,
        "environment": {"python": sys.version.split()[0], "platform": platform.system()},
        "reproducibility": "limited", "execution_mode": settings.execution_mode,
        "judge": {"endpoint": settings.judge_endpoint, "model": settings.judge_model, "prompt_version": "judge-1.0", "max_calls": settings.judge_max_calls},
    }


from agenteval.durable import LocalRunner


def report(session, batch):
    run = session.get(Run, batch.run_id)
    assessments = records(session, TrialAssessment, batch_id=batch.id)
    results = records(session, EvaluationResult, batch_id=batch.id)
    trials = records(session, Trial, run_id=run.id)
    all_executions = list(session.scalars(select(Execution).join(Trial, Execution.trial_id == Trial.id).where(Trial.run_id == run.id)))
    trial_map = {t.id: t for t in trials}
    case_map = {c.id: c for c in session.scalars(select(TestCase).where(TestCase.benchmark_version_id == run.benchmark_version_id))}
    by_execution = {e.id: e for e in all_executions}
    executions = [by_execution[a.selected_execution_id] for a in assessments]
    counts = Counter(a.verdict for a in assessments)
    p, f = counts["passed"], counts["failed"]
    n = len(trials)
    rows = []
    for assessment in assessments:
        trial = trial_map[assessment.trial_id]
        case = case_map[trial.case_id]
        rows.append({"trial_id": trial.id, "case_key": case.case_key,
                     "repetition_index": trial.repetition_index, "verdict": assessment.verdict,
                     "reasons": assessment.reasons, "execution_id": assessment.selected_execution_id,
                     "execution_status": by_execution[assessment.selected_execution_id].status})
    latencies = [e.latency_ms for e in executions if e.status == "completed" and e.latency_ms is not None]
    from agenteval.metrics import specialty_metrics
    selected_ids = {e.id for e in executions}
    selected_results = [{**r.result, "execution_id": r.execution_id} for r in results if r.execution_id in selected_ids]
    specialized = specialty_metrics(batch.grading_manifest.get("evaluators", []), selected_results, executions, all_executions, trials, rows)
    expected_scores = len(all_executions) * len(batch.grading_manifest.get("evaluators", []))
    expected_trial_scores = len(trials) * len(batch.grading_manifest.get("evaluators", []))
    return {
        "run_id": run.id, "grading_batch_id": batch.id, "grading_status": batch.status,
        "run_status": run.status, "manifest_hash": run.manifest_hash,
        "counts": {k: counts[k] for k in ("passed", "failed", "unknown", "excluded")},
        "planned_trials": n, "success_rate": p / (p + f) if p + f else None,
        "decision_coverage": (p + f) / n if n else 0,
        "confirmed_success_fraction": p / n if n else 0,
        "scoring_coverage": sum(r.result["grading_status"] == "scored" for r in results) / expected_scores if expected_scores else 0,
        "attempt_scoring_coverage": sum(r.result["grading_status"] == "scored" for r in results) / expected_scores if expected_scores else 0,
        "trial_scoring_coverage": sum(r["grading_status"] == "scored" for r in selected_results) / expected_trial_scores if expected_trial_scores else 0,
        "metric_definitions": {"scoring_coverage": "attempt_scoring_coverage (legacy alias)", "attempt_scoring_coverage": "scored results / (all attempts * evaluator instances)",
                               "trial_scoring_coverage": "canonical scored results / (planned trials * evaluator instances)",
                               "success_rate": "passed trials / decided trials; unknown and excluded remain in decision coverage"},
        "latency_ms": {"p50": nearest_rank(latencies, .5), "p95": nearest_rank(latencies, .95), "samples": len(latencies)},
        "execution_counts": dict(Counter(e.status for e in all_executions)), "trials": rows,
        "attempts": len(all_executions), "retry_rate": len({e.trial_id for e in all_executions if e.attempt_index > 0}) / n if n else 0,
        "hard_constraint_violations": sum(r.result["hard_constraint"] and r.result["passed"] is False for r in results),
        "specialty_metrics": specialized,
    }


def compare(session, baseline_batch, candidate_batch, mode):
    baseline = session.get(Run, baseline_batch.run_id)
    candidate = session.get(Run, candidate_batch.run_id)
    differences = []
    for name in ("benchmark", "environment"):
        if baseline.manifest[name] != candidate.manifest[name]:
            differences.append(name)
    for name in ("evaluators", "execution", "gate", "judge", "engine_version", "environment"):
        if baseline_batch.grading_manifest.get(name) != candidate_batch.grading_manifest.get(name):
            differences.append(name)
    b, c = report(session, baseline_batch), report(session, candidate_batch)
    delta = c["success_rate"] - b["success_rate"] if c["success_rate"] is not None and b["success_rate"] is not None else None
    def by_case(rows):
        data = {}
        for row in rows:
            data.setdefault(row["case_key"], []).append(row["verdict"])
        return data
    old, new = by_case(b["trials"]), by_case(c["trials"])
    added, fixed, uncertain = [], [], []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key, []), new.get(key, [])
        if not before or not after or any(x in ("unknown", "excluded") for x in before + after):
            uncertain.append(key)
        elif set(before) == {"passed"} and set(after) == {"failed"}:
            added.append(key)
        elif set(before) == {"failed"} and set(after) == {"passed"}:
            fixed.append(key)
        elif before.count("passed") / len(before) != after.count("passed") / len(after):
            uncertain.append(key)
    gate = candidate_batch.grading_manifest["gate"]
    reasons = []
    core = set(gate["core_case_keys"])
    core_failed = any(r["case_key"] in core and r["verdict"] == "failed" for r in c["trials"])
    from agenteval.metrics import extra_gate
    extra_failed, extra_missing = extra_gate(gate, b, c)
    if mode != "strict" or differences:
        status, reasons = "inconclusive", ["EXPLORATORY_OR_INCOMPATIBLE"]
    elif c["hard_constraint_violations"] or core_failed:
        status, reasons = "failed", ["HARD_CONSTRAINT_OR_CORE_CASE_FAILURE"]
    elif extra_failed:
        status, reasons = "failed", extra_failed
    elif min(b["decision_coverage"], c["decision_coverage"]) < gate["min_decision_coverage"] or delta is None or uncertain:
        status, reasons = "inconclusive", ["INCOMPLETE_OR_VARIABLE_EVIDENCE"]
    elif c["success_rate"] < gate["min_success_rate"] or delta < -gate["max_success_rate_drop"]:
        status, reasons = "failed", ["SUCCESS_RATE_THRESHOLD"]
    elif extra_missing:
        status, reasons = "inconclusive", extra_missing
    else:
        status = "passed"
    return {"baseline_batch_id": baseline_batch.id, "candidate_batch_id": candidate_batch.id,
            "mode": mode, "compatible": not differences, "differences": differences,
            "baseline": b, "candidate": c, "success_rate_delta": delta,
            "new_failures": added, "fixed_cases": fixed, "uncertain_cases": uncertain,
            "agent_changes": {"baseline": baseline.manifest["agent"], "candidate": candidate.manifest["agent"]},
            "metric_deltas": metric_deltas(b, c),
            "gate": {"status": status, "reasons": reasons}}


def metric_deltas(baseline, candidate):
    def collect(value, prefix=""):
        values = {}
        for key, item in value.items():
            path = f"{prefix}/{key}"
            if isinstance(item, dict):
                values.update(collect(item, path))
            elif type(item) in (int, float):
                values[path] = item
        return values
    old, new = collect(baseline["specialty_metrics"]), collect(candidate["specialty_metrics"])
    # Per-instance metrics remain independently comparable; never average four grains.
    for grain in ("e2e", "trajectory", "tool", "component"):
        for root, report in ((old, baseline), (new, candidate)):
            for item in report["specialty_metrics"]["granularities"][grain]["evaluators"]:
                if item["mean_score"] is not None:
                    root[f"/{grain}/{item['instance_id']}/mean_score"] = item["mean_score"]
    return {key: {"baseline": old.get(key), "candidate": new.get(key), "delta": new[key] - old[key] if key in old and key in new else None}
            for key in sorted(set(old) | set(new))}

