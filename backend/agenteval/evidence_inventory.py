"""Database-side inventory of project evidence and frozen release references."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import LargeBinary, Text, cast, false, func, select

from agenteval.db import EvaluationResult, Execution, GradingBatch, ReleaseDecision, Run, TraceSpan, Trial


def run_inventory(project_id, days):
    snapshot = ReleaseDecision.snapshot["comparison"]
    references = select(snapshot["baseline"]["run_id"].as_string().label("run_id")).where(ReleaseDecision.project_id == project_id).union(
        select(snapshot["candidate"]["run_id"].as_string().label("run_id")).where(ReleaseDecision.project_id == project_id)
    ).subquery()
    expired = Run.created_at < datetime.now(timezone.utc) - timedelta(days=days) if days is not None else false()
    return (select(Run.id, Run.created_at, Run.status, expired.label("past_retention"),
        references.c.run_id.is_not(None).label("release_referenced"))
        .outerjoin(references, references.c.run_id == Run.id)
        .where(Run.project_id == project_id))


def payload_inventory(session, project_id):
    def size(column):
        value = cast(column, Text)
        if session.bind.dialect.name == "postgresql":
            return func.octet_length(value)
        return func.length(cast(value, LargeBinary))

    def total(expression):
        return func.coalesce(func.sum(expression), 0)

    executions = select(Execution.id).join(Trial).join(Run).where(Run.project_id == project_id)
    response, state, response_chars = session.execute(select(total(size(Execution.response)),
        total(size(Execution.environment_evidence)), total(func.length(cast(Execution.response, Text))))
        .where(Execution.id.in_(executions))).one()
    trace, trace_chars = session.execute(select(total(size(TraceSpan.record)), total(func.length(cast(TraceSpan.record, Text))))
        .where(TraceSpan.execution_id.in_(executions))).one()
    scores = session.scalar(select(total(size(EvaluationResult.result))).join(GradingBatch).join(Run)
        .where(Run.project_id == project_id))
    return {"responses": response, "trace": trace, "state": state, "scores": scores}, response_chars + trace_chars


def summary(session, project_id, days, warning_bytes):
    runs = run_inventory(project_id, days).subquery()
    count, held, overdue, overdue_held = session.execute(select(func.count(),
        func.count().filter(runs.c.release_referenced), func.count().filter(runs.c.past_retention),
        func.count().filter(runs.c.past_retention, runs.c.release_referenced)).select_from(runs)).one()
    payloads, characters = payload_inventory(session, project_id)
    size = sum(payloads.values())
    return {
        "project_id": project_id, "runs": count, "release_referenced_runs": held, "retention_days": days,
        "past_retention_runs": overdue, "past_retention_release_holds": overdue_held,
        "response_and_trace_json_characters": characters,
        "capacity_scope": "Serialized response and trace character count; not physical disk bytes or all evidence tables.",
        "evidence_json_bytes": size, "evidence_json_bytes_by_kind": payloads, "evidence_warning_bytes": warning_bytes,
        "evidence_json_bytes_scope": "Database JSON text bytes for responses, trace, state snapshots and scoring results.",
        "warning_reached": warning_bytes is not None and size >= warning_bytes,
        "automatic_deletion": False, "archive_mode": "non_destructive_project_snapshot",
        "scope": "Age is advisory; fixed evidence and original rows are retained.",
    }
