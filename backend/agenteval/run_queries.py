"""Paged task queries and fixed-batch quality summaries, independent of UI state."""
from sqlalchemy import case, func, or_, select

from agenteval.db import (Agent, AgentVersion, Benchmark, BenchmarkVersion, EvaluationResult,
    Execution, GradingBatch, Run, Trial, TrialAssessment)


def summary_query(batch_id=None):
    ranked = select(GradingBatch.id.label("id"), func.row_number().over(partition_by=GradingBatch.run_id,
        order_by=(GradingBatch.revision.desc(), GradingBatch.id)).label("position")).subquery()
    planned = select(Trial.run_id, func.count().label("planned")).group_by(Trial.run_id).subquery()
    verdicts = select(TrialAssessment.batch_id, *[func.sum(case((TrialAssessment.verdict == v, 1), else_=0)).label(v)
        for v in ("passed", "failed", "unknown", "excluded")], func.max(TrialAssessment.created_at).label("updated"))
    verdicts = verdicts.group_by(TrialAssessment.batch_id).subquery()
    scores = select(EvaluationResult.batch_id, func.sum(case((EvaluationResult.result["grading_status"].as_string() == "scored", 1), else_=0)).label("scored"))
    scores = scores.join(TrialAssessment, (TrialAssessment.batch_id == EvaluationResult.batch_id) &
        (TrialAssessment.selected_execution_id == EvaluationResult.execution_id)).group_by(EvaluationResult.batch_id).subquery()
    duration = select(Trial.run_id, func.sum(Execution.latency_ms).label("duration_ms")).join(Execution, Execution.trial_id == Trial.id).group_by(Trial.run_id).subquery()
    n = func.coalesce(planned.c.planned, 0)
    p, f, u, x = [func.coalesce(getattr(verdicts.c, k), 0) for k in ("passed", "failed", "unknown", "excluded")]
    expected = n * func.coalesce(func.json_array_length(GradingBatch.grading_manifest["evaluators"]), 0)
    scored = func.coalesce(scores.c.scored, 0)
    quality = case((or_(GradingBatch.id.is_(None), GradingBatch.status != "completed"), "pending"),
        (f > 0, "failed"), ((n > 0) & (p == n) & (u == 0) & (x == 0) & (expected > 0) & (scored == expected), "passed"), else_="insufficient")
    query = select(Run, Agent.name.label("agent_name"), AgentVersion.version.label("agent_version"),
        Benchmark.name.label("benchmark_name"), BenchmarkVersion.version.label("benchmark_version"),
        GradingBatch.id.label("batch_id"), GradingBatch.revision, GradingBatch.status.label("grading_status"),
        func.coalesce(verdicts.c.updated, GradingBatch.created_at).label("result_updated_at"),
        n.label("planned"), p.label("passed"), f.label("failed"), u.label("unknown"), x.label("excluded"),
        scored.label("scored"), expected.label("expected_scores"), quality.label("quality_status"), duration.c.duration_ms)
    query = query.join(AgentVersion, AgentVersion.id == Run.agent_version_id).join(Agent, Agent.id == AgentVersion.agent_id)
    query = query.join(BenchmarkVersion, BenchmarkVersion.id == Run.benchmark_version_id).join(Benchmark, Benchmark.id == BenchmarkVersion.benchmark_id)
    query = query.outerjoin(GradingBatch, (GradingBatch.run_id == Run.id) & (GradingBatch.id == batch_id if batch_id else GradingBatch.id.in_(select(ranked.c.id).where(ranked.c.position == 1))))
    return query.outerjoin(planned, planned.c.run_id == Run.id).outerjoin(verdicts, verdicts.c.batch_id == GradingBatch.id).outerjoin(
        scores, scores.c.batch_id == GradingBatch.id).outerjoin(duration, duration.c.run_id == Run.id), quality


def summary(row):
    counts = {k: row[k] for k in ("passed", "failed", "unknown", "excluded")}
    return {"grading_batch_id": row["batch_id"], "revision": row["revision"], "grading_status": row["grading_status"],
        "quality_status": row["quality_status"], "planned_trials": row["planned"], "counts": counts,
        "unassessed_trials": max(0, row["planned"] - sum(counts.values())),
        "trial_scoring_coverage": row["scored"] / row["expected_scores"] if row["expected_scores"] else None,
        "updated_at": row["result_updated_at"].isoformat() if row["result_updated_at"] else None, "duration_ms": row["duration_ms"],
        "duration_scope": "recorded_attempt_latency_sum_not_wall_clock",
        "initiator": row["Run"].initiator or {"id": None, "display_name": "共享团队身份（未记录个人）", "kind": "unattributed_team"}}


def run_view(row, serialize):
    return {**serialize(row["Run"]), **{k: row[k] for k in ("agent_name", "agent_version", "benchmark_name", "benchmark_version")}, "quality_summary": summary(row)}


def batch_summary(session, run_id, batch_id):
    query, _ = summary_query(batch_id)
    return summary(session.execute(query.where(Run.id == run_id)).mappings().one())


def page_runs(session, serialize, project_id, offset, limit, q="", status=None, quality=None, version=None,
              created_after=None, created_before=None, sort="newest"):
    query, verdict = summary_query()
    if project_id: query = query.where(Run.project_id == project_id)
    if q:
        query = query.where(or_(*[func.lower(field).contains(q.lower(), autoescape=True) for field in (Run.id, Agent.name, Benchmark.name)]))
    if status: query = query.where(Run.status == status)
    if quality: query = query.where(verdict == quality)
    if version: query = query.where(AgentVersion.version == version)
    if created_after: query = query.where(Run.created_at >= created_after)
    if created_before: query = query.where(Run.created_at <= created_before)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    query = query.order_by(Run.created_at.asc() if sort == "oldest" else Run.created_at.desc(), Run.id)
    return {"items": [run_view(row, serialize) for row in session.execute(query.offset(offset).limit(limit)).mappings()],
        "total": total, "offset": offset, "limit": limit}
