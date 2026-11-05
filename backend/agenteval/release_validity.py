"""Current relevance is derived separately; historical release snapshots remain immutable."""
from datetime import datetime, timezone
from sqlalchemy import select
from agenteval import __version__
from agenteval.db import AgentVersion, Comparison, GradingBatch, Run
from agenteval.issue_scopes import INACTIVE, applicable_records


def current_validity(session, decision):
    comparison = session.get(Comparison, decision.comparison_id)
    reasons = []
    baseline, candidate = (session.get(GradingBatch, comparison.report[k]) for k in ("baseline_batch_id", "candidate_batch_id"))
    for batch in (baseline,candidate):
        if session.scalar(select(GradingBatch.id).where(GradingBatch.run_id == batch.run_id, GradingBatch.revision > batch.revision).limit(1)):
            reasons.append("NEWER_GRADING_BATCH_EXISTS")
        if batch.grading_manifest.get("engine_version") != __version__: reasons.append("ENGINE_REASSESSMENT_REQUIRED")
    run = session.get(Run, candidate.run_id); version = session.get(AgentVersion, run.agent_version_id)
    blockers = {i.id:i.revision for i,_ in applicable_records(session, run.project_id, version.agent_id, version.id, run.benchmark_version_id)
        if i.kind == "issue" and i.severity == "blocking" and i.status not in INACTIVE}
    previous = {i["id"]:i["revision"] for i in decision.snapshot["blocking_issues"]}
    if blockers != previous: reasons.append("APPLICABLE_BLOCKING_ISSUES_CHANGED")
    return {"current": not reasons, "reasons": sorted(set(reasons)), "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Current relevance only; never edits the historical status or frozen snapshot, and does not authorize production deployment."}
