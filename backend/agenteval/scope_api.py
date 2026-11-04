"""Shared evaluation scope and accountable anomaly triage."""
from collections import Counter
from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import aliased

from agenteval.db import (Agent, AgentVersion, BenchmarkVersion, Comparison, GradingBatch, IssueEvent,
    IssueScope, Project, QualityIssue, QualityPolicy, ReleaseDecision, Run)
from agenteval.issue_scopes import INACTIVE, applicable_records, ingestion_mode, scope_for
from agenteval.product_contracts import BulkIssues, IngestionPolicy, ScopeUpdate
from agenteval.service import report


def resolve_scope(session, get, project_id, agent_id=None, candidate_version_id=None, baseline_version_id=None, benchmark_version_id=None):
    get(session, Project, project_id)
    if not any((agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)):
        run = session.scalar(select(Run).where(Run.project_id == project_id, Run.status.in_(["completed", "cancelled"])).order_by(Run.created_at.desc(), Run.id))
        if not run:
            return None
        candidate_version_id, benchmark_version_id = run.agent_version_id, run.benchmark_version_id
        agent_id = get(session, AgentVersion, candidate_version_id).agent_id
    if not all((agent_id, candidate_version_id, benchmark_version_id)):
        raise HTTPException(422, detail={"code": "INCOMPLETE_EVALUATION_SCOPE"})
    agent = get(session, Agent, agent_id)
    candidate = get(session, AgentVersion, candidate_version_id)
    benchmark = get(session, BenchmarkVersion, benchmark_version_id)
    from agenteval.db import Benchmark
    if agent.project_id != project_id or candidate.agent_id != agent.id or get(session, Benchmark, benchmark.benchmark_id).project_id != project_id:
        raise HTTPException(422, detail={"code": "EVALUATION_SCOPE_MISMATCH"})
    if baseline_version_id and get(session, AgentVersion, baseline_version_id).agent_id != agent_id:
        raise HTTPException(422, detail={"code": "EVALUATION_SCOPE_MISMATCH"})
    return {"agent_id": agent_id, "candidate_version_id": candidate_version_id, "baseline_version_id": baseline_version_id,
        "benchmark_id": benchmark.benchmark_id, "benchmark_version_id": benchmark_version_id,
        "agent_name": agent.name, "candidate_label": f"{agent.name} · v{candidate.version}"}


def comparison_query(scope, decision=False):
    candidate, baseline = aliased(Run), aliased(Run)
    query = select(ReleaseDecision if decision else Comparison)
    if decision:
        query = query.join(Comparison, Comparison.id == ReleaseDecision.comparison_id)
    query = query.join(candidate, candidate.id == Comparison.report["candidate"]["run_id"].as_string()).join(
        baseline, baseline.id == Comparison.report["baseline"]["run_id"].as_string()).join(AgentVersion, AgentVersion.id == baseline.agent_version_id)
    query = query.where(candidate.agent_version_id == scope["candidate_version_id"], candidate.benchmark_version_id == scope["benchmark_version_id"],
        baseline.benchmark_version_id == scope["benchmark_version_id"], AgentVersion.agent_id == scope["agent_id"])
    if scope.get("baseline_version_id"):
        query = query.where(baseline.agent_version_id == scope["baseline_version_id"])
    else:
        query = query.where(False)
    return query


def install(router, database, get, serialize, runner, issue_view):
    @router.get("/quality/policy/{project_id}")
    def get_policy(project_id: str, session=Depends(database)):
        get(session, Project, project_id)
        return {"ingestion_mode": ingestion_mode(session, project_id)}

    @router.post("/quality/policy/{project_id}")
    def set_policy(project_id: str, body: IngestionPolicy, session=Depends(database)):
        get(session, Project, project_id); session.rollback()
        with runner.coordinator.locked(project_id) as locked:
            policy = locked.scalar(select(QualityPolicy).filter_by(project_id=project_id))
            if not policy:
                policy = QualityPolicy(project_id=project_id)
                locked.add(policy)
            policy.ingestion_mode = body.ingestion_mode
        return body.model_dump()

    @router.post("/quality/issues/bulk")
    def bulk(body: BulkIssues):
        with runner.coordinator.locked(body.project_id) as session:
            rows = [get(session, QualityIssue, key) for key in body.issue_ids]
            if any(i.project_id != body.project_id for i in rows):
                raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
            if any(i.revision != body.expected_revisions[i.id] for i in rows):
                raise HTTPException(409, detail={"code": "ISSUE_REVISION_CONFLICT"})
            if body.action in ("confirm", "ignore") and any(i.kind != "anomaly" or i.status != "open" for i in rows):
                raise HTTPException(409, detail={"code": "ANOMALY_TRIAGE_ONLY"})
            if body.action == "merge":
                if len(rows) < 2 or any(i.status in INACTIVE for i in rows):
                    raise HTTPException(422, detail={"code": "INVALID_MERGE"})
                scopes = {i.id: scope_for(session, i.id) for i in rows}
                if len({(s.agent_id, s.benchmark_id, s.case_hash) for s in scopes.values()}) != 1:
                    raise HTTPException(422, detail={"code": "MERGE_SCOPE_MISMATCH"})
                target = next(i for i in rows if i.id == body.target_issue_id)
                target_scope = scopes[target.id]
                target_scope.version_ids = sorted({v for s in scopes.values() for v in s.version_ids})
                if any(i.kind == "issue" for i in rows):
                    target.kind = "issue"
                    if target.status == "open": target.status = "confirmed"
                target.severity = "blocking" if any(i.severity == "blocking" for i in rows) else target.severity
                for i in rows:
                    if i.id != target.id:
                        i.status, i.merged_into_id = "merged", target.id
            for i in rows:
                if body.action == "confirm": i.kind, i.status = "issue", "confirmed"
                if body.action == "ignore": i.status = "ignored"
                if body.action == "assign":
                    from agenteval.security import assign_member
                    assign_member(session, i, body.assignee_user_id, body.owner)
                i.revision += 1
                session.add(IssueEvent(issue_id=i.id, action=f"bulk_{body.action}", detail={"note": body.note, "owner": body.owner, "target_issue_id": body.target_issue_id, "source_issue_ids": body.issue_ids}))
            session.flush()
            return {"items": [serialize(i) for i in rows]}

    @router.post("/quality/issues/{issue_id}/scope")
    def update_scope(issue_id: str, body: ScopeUpdate, session=Depends(database)):
        issue = get(session, QualityIssue, issue_id); project_id = issue.project_id; session.rollback()
        with runner.coordinator.locked(project_id) as locked:
            issue = get(locked, QualityIssue, issue_id)
            if issue.revision != body.expected_revision:
                raise HTTPException(409, detail={"code": "ISSUE_REVISION_CONFLICT"})
            if issue.status in ("ignored", "merged", "resolved"):
                raise HTTPException(409, detail={"code": "INACTIVE_ISSUE_SCOPE"})
            scope = scope_for(locked, issue.id)
            versions = [get(locked, AgentVersion, v) for v in set(body.version_ids)]
            if any(v.agent_id != scope.agent_id for v in versions):
                raise HTTPException(422, detail={"code": "EVALUATION_SCOPE_MISMATCH"})
            before = scope.version_ids
            scope.version_ids = sorted(v.id for v in versions); issue.revision += 1
            locked.add(IssueEvent(issue_id=issue.id, action="scope_changed", detail={"before": before, "after": scope.version_ids, "note": body.note}))
            locked.flush()
            return {**serialize(issue), "issue_scope": serialize(scope)}

    @router.get("/quality/workbench/{project_id}")
    def workbench(project_id: str, agent_id: str | None = None, candidate_version_id: str | None = None,
        baseline_version_id: str | None = None, benchmark_version_id: str | None = None, session=Depends(database)):
        scope = resolve_scope(session, get, project_id, agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)
        if not scope:
            return {"evaluation_scope": None, "open_issues": 0, "pending_anomalies": 0, "blocking_issues": 0, "candidate": None,
                "comparison": None, "latest_decision": None, "priorities": [], "version_labels": {}, "scope": "尚无已完成评测。"}
        records = applicable_records(session, project_id, scope["agent_id"], scope["candidate_version_id"], scope["benchmark_version_id"])
        active = [i for i, _ in records if i.status not in INACTIVE]
        formal = [i for i in active if i.kind == "issue"]
        comparison = session.scalar(comparison_query(scope).order_by(Comparison.created_at.desc(), Comparison.id))
        latest = session.scalar(select(GradingBatch).join(Run, Run.id == GradingBatch.run_id).where(Run.project_id == project_id,
            Run.agent_version_id == scope["candidate_version_id"], Run.benchmark_version_id == scope["benchmark_version_id"],
            GradingBatch.status == "completed").order_by(GradingBatch.created_at.desc(), GradingBatch.id))
        candidate = comparison.report["candidate"] if comparison else report(session, latest) if latest else None
        decision = session.scalar(select(ReleaseDecision).where(ReleaseDecision.comparison_id == comparison.id).order_by(ReleaseDecision.created_at.desc(), ReleaseDecision.id)) if comparison else None
        labels = {"candidate": scope["candidate_label"]}
        if scope["baseline_version_id"]:
            labels["baseline"] = f"{scope['agent_name']} · v{get(session, AgentVersion, scope['baseline_version_id']).version}"
        active.sort(key=lambda i: (0 if i.kind == "issue" and i.severity == "blocking" else 1 if i.kind == "issue" else 2, str(i.created_at), i.id))
        from agenteval.comparison_context import views
        return {"evaluation_scope": scope, "open_issues": len(formal), "pending_anomalies": sum(i.kind == "anomaly" for i in active),
            "issue_counts": dict(Counter(i.status for i, _ in records)), "blocking_issues": sum(i.severity == "blocking" for i in formal),
            "candidate": candidate, "comparison": views(session, [comparison], serialize)[0] if comparison else None, "latest_decision": serialize(decision) if decision else None,
            "version_labels": labels, "priorities": [issue_view(session, i) for i in active[:5]],
            "scope": "所选 Agent、候选/基线版本及 Benchmark 固定版本；指标与适用问题使用同一范围。"}

    @router.get("/quality/project-overview/{project_id}")
    def project_overview(project_id: str, session=Depends(database)):
        get(session, Project, project_id)
        rows = list(session.scalars(select(QualityIssue).filter_by(project_id=project_id)))
        active = [i for i in rows if i.status not in INACTIVE]
        return {"project_id": project_id, "pending_anomalies": sum(i.kind == "anomaly" for i in active),
            "open_issues": sum(i.kind == "issue" for i in active), "blocking_issues": sum(i.kind == "issue" and i.severity == "blocking" for i in active),
            "resolved": sum(i.status == "resolved" for i in rows), "ignored": sum(i.status == "ignored" for i in rows),
            "ingestion_mode": ingestion_mode(session, project_id), "scope": "全项目汇总，不是单一版本的发布建议。"}
