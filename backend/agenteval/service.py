import asyncio
import platform
import sys
import time
from collections import Counter

import httpx
from sqlalchemy import select

from agenteval import __version__
from agenteval.adapters import AgentFailure, invoke
from agenteval.contracts import EvaluatorSpec, digest, nearest_rank
from agenteval.db import (Agent, AgentVersion, BenchmarkVersion, EvaluationResult, Execution,
                         GradingBatch, ProfileVersion, Run, TestCase, Trial, TrialAssessment)
from agenteval.evaluators import evaluate, verdict


def records(session, model, **filters):
    return list(session.scalars(select(model).filter_by(**filters).order_by(model.created_at, model.id)))


def snapshot(session, run):
    agent_version = session.get(AgentVersion, run.agent_version_id)
    agent = session.get(Agent, agent_version.agent_id)
    benchmark = session.get(BenchmarkVersion, run.benchmark_version_id)
    profile = session.get(ProfileVersion, run.profile_version_id)
    return {
        "schema_version": "0.1", "engine_version": __version__,
        "agent": {"version_id": agent_version.id, "adapter_type": agent.adapter_type,
                  "configuration": agent_version.configuration, "capabilities": agent_version.capabilities,
                  "content_hash": agent_version.content_hash},
        "benchmark": {"version_id": benchmark.id, "content_hash": benchmark.content_hash},
        "profile": profile.definition, "profile_hash": profile.content_hash,
        "environment": {"python": sys.version.split()[0], "platform": platform.system()},
        "reproducibility": "limited", "execution_mode": "in_process_coordinator",
    }


class LocalRunner:
    """M1 coordinator only. No broker, automatic retries, or distributed leases."""
    def __init__(self, sessions, settings):
        self.sessions = sessions
        self.settings = settings
        self.capacity = asyncio.Semaphore(8)

    async def execute(self, run_id, batch_id):
        with self.sessions() as session:
            run = session.get(Run, run_id)
            run.status = "running"
            policy = run.manifest["profile"]["execution"]
            trial_ids = [t.id for t in records(session, Trial, run_id=run_id)]
            session.commit()
        semaphore = asyncio.Semaphore(policy["max_concurrency"])

        async def one(trial_id):
            async with self.capacity, semaphore:
                await self.execute_trial(run_id, trial_id, policy["timeout_seconds"])

        try:
            await asyncio.gather(*(one(t) for t in trial_ids))
            with self.sessions() as session:
                run = session.get(Run, run_id)
                run.status = "evaluating"
                session.commit()
            self.grade(run_id, batch_id)
            with self.sessions() as session:
                run = session.get(Run, run_id)
                run.status = "cancelled" if run.cancel_requested else "completed"
                session.commit()
        except Exception:
            with self.sessions() as session:
                session.get(Run, run_id).status = "failed"
                session.get(GradingBatch, batch_id).status = "failed"
                session.commit()
            raise

    async def execute_trial(self, run_id, trial_id, timeout):
        with self.sessions() as session:
            run = session.get(Run, run_id)
            trial = session.get(Trial, trial_id)
            execution = records(session, Execution, trial_id=trial_id)[0]
            if run.cancel_requested:
                execution.status = "cancelled"
                session.commit()
                return
            case = session.get(TestCase, trial.case_id)
            agent = run.manifest["agent"]
            request = {"run_id": run_id, "trial_id": trial_id, "execution_id": execution.id,
                       "input": case.definition["input"], "case_key": case.case_key,
                       "session_id": execution.id, "trace_enabled": True}
            execution.status = "invoking"
            execution_id = execution.id
            session.commit()
        started = time.monotonic()
        response, error = None, None
        try:
            async with asyncio.timeout(timeout):
                response = await invoke(agent["adapter_type"], agent["configuration"],
                                        request, timeout, self.settings)
            status = "completed"
        except (TimeoutError, httpx.TimeoutException):
            status, error = "timed_out", {"code": "AGENT_TIMEOUT", "remote_outcome": "unknown"}
        except AgentFailure:
            status, error = "agent_failed", {"code": "AGENT_PROTOCOL_OR_EXECUTION_ERROR"}
        except httpx.RequestError:
            # A connection error cannot prove the remote endpoint was never invoked.
            status, error = "outcome_unknown", {"code": "HTTP_TRANSPORT_ERROR"}
        except Exception:
            status, error = "infrastructure_failed", {"code": "LOCAL_EXECUTOR_ERROR"}
        with self.sessions() as session:
            execution = session.get(Execution, execution_id)
            execution.status = status
            execution.response, execution.error = response, error
            execution.latency_ms = round((time.monotonic() - started) * 1000, 3)
            session.commit()

    def grade(self, run_id, batch_id):
        with self.sessions() as session:
            batch = session.get(GradingBatch, batch_id)
            batch.status = "running"
            specs = [EvaluatorSpec.model_validate(x) for x in batch.grading_manifest["evaluators"]]
            for trial in records(session, Trial, run_id=run_id):
                execution = records(session, Execution, trial_id=trial.id)[0]
                case = session.get(TestCase, trial.case_id)
                results = [evaluate(spec, case.definition, execution.response or {}) for spec in specs]
                for item in results:
                    session.add(EvaluationResult(batch_id=batch_id, execution_id=execution.id,
                                                 instance_id=item["instance_id"], result=item))
                decision, reasons = verdict(execution.status, results)
                session.add(TrialAssessment(trial_id=trial.id, batch_id=batch_id,
                                            selected_execution_id=execution.id, verdict=decision, reasons=reasons))
            batch.status = "completed"
            session.commit()


def report(session, batch):
    run = session.get(Run, batch.run_id)
    assessments = records(session, TrialAssessment, batch_id=batch.id)
    results = records(session, EvaluationResult, batch_id=batch.id)
    trials = records(session, Trial, run_id=run.id)
    executions = [records(session, Execution, trial_id=t.id)[0] for t in trials]
    counts = Counter(a.verdict for a in assessments)
    p, f = counts["passed"], counts["failed"]
    n = len(trials)
    by_execution = {e.id: e for e in executions}
    rows = []
    for assessment in assessments:
        trial = session.get(Trial, assessment.trial_id)
        case = session.get(TestCase, trial.case_id)
        rows.append({"trial_id": trial.id, "case_key": case.case_key,
                     "repetition_index": trial.repetition_index, "verdict": assessment.verdict,
                     "reasons": assessment.reasons, "execution_id": assessment.selected_execution_id,
                     "execution_status": by_execution[assessment.selected_execution_id].status})
    latencies = [e.latency_ms for e in executions if e.status == "completed" and e.latency_ms is not None]
    return {
        "run_id": run.id, "grading_batch_id": batch.id, "grading_status": batch.status,
        "run_status": run.status, "manifest_hash": run.manifest_hash,
        "counts": {k: counts[k] for k in ("passed", "failed", "unknown", "excluded")},
        "planned_trials": n, "success_rate": p / (p + f) if p + f else None,
        "decision_coverage": (p + f) / n if n else 0,
        "confirmed_success_fraction": p / n if n else 0,
        "scoring_coverage": sum(r.result["grading_status"] == "scored" for r in results) / len(results) if results else 0,
        "latency_ms": {"p50": nearest_rank(latencies, .5), "p95": nearest_rank(latencies, .95), "samples": len(latencies)},
        "execution_counts": dict(Counter(e.status for e in executions)), "trials": rows,
        "safety_violations": sum(r.result["hard_constraint"] and r.result["passed"] is False for r in results),
    }


def compare(session, baseline_batch, candidate_batch, mode):
    baseline = session.get(Run, baseline_batch.run_id)
    candidate = session.get(Run, candidate_batch.run_id)
    differences = []
    for name in ("benchmark", "environment"):
        if baseline.manifest[name] != candidate.manifest[name]:
            differences.append(name)
    for name in ("evaluators", "execution", "gate"):
        if baseline_batch.grading_manifest[name] != candidate_batch.grading_manifest[name]:
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
    if mode != "strict" or differences:
        status, reasons = "inconclusive", ["EXPLORATORY_OR_INCOMPATIBLE"]
    elif c["safety_violations"] or core_failed:
        status, reasons = "failed", ["HARD_CONSTRAINT_OR_CORE_CASE_FAILURE"]
    elif min(b["decision_coverage"], c["decision_coverage"]) < gate["min_decision_coverage"] or delta is None or uncertain:
        status, reasons = "inconclusive", ["INCOMPLETE_OR_VARIABLE_EVIDENCE"]
    elif c["success_rate"] < gate["min_success_rate"] or delta < -gate["max_success_rate_drop"]:
        status, reasons = "failed", ["SUCCESS_RATE_THRESHOLD"]
    else:
        status = "passed"
    return {"baseline_batch_id": baseline_batch.id, "candidate_batch_id": candidate_batch.id,
            "mode": mode, "compatible": not differences, "differences": differences,
            "baseline": b, "candidate": c, "success_rate_delta": delta,
            "new_failures": added, "fixed_cases": fixed, "uncertain_cases": uncertain,
            "agent_changes": {"baseline": baseline.manifest["agent"], "candidate": candidate.manifest["agent"]},
            "gate": {"status": status, "reasons": reasons}}

