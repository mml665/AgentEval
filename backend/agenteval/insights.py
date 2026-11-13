"""Project notifications and descriptive Trial-level quality trends."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone

from fastapi import Depends, HTTPException, Query
from sqlalchemy import and_, case, func, literal, select, union_all

from agenteval.db import (Agent, AgentVersion, AuditEvent, Benchmark, BenchmarkVersion,
    GradingBatch, IssueEvent, ProfileVersion, Project, QualityIssue, Run, TestCase, Trial, TrialAssessment)


def trial_metrics(planned, passed, failed):
    decided = passed + failed
    return {"planned_trials": planned, "passed": passed, "failed": failed,
            "success_rate": passed / decided if decided else None,
            "decision_coverage": decided / planned if planned else None}


def install(router, database, get):
    @router.get("/quality/notifications/{project_id}")
    def notifications(project_id: str, offset: int = Query(0, ge=0),
                      limit: int = Query(25, ge=1, le=100), session=Depends(database)):
        get(session, Project, project_id)
        task = (select(AuditEvent.id, AuditEvent.created_at, literal("task").label("kind"),
                      Run.id.label("target_id"), AuditEvent.action, Agent.name.label("title"),
                      AuditEvent.detail).select_from(AuditEvent).join(Run, Run.id == AuditEvent.run_id)
                      .join(AgentVersion, Run.agent_version_id == AgentVersion.id).join(Agent, AgentVersion.agent_id == Agent.id).where(
                          Run.project_id == project_id, AuditEvent.action == "GRADING_BATCH_COMPLETED"))
        issue = select(IssueEvent.id, IssueEvent.created_at, literal("issue"), IssueEvent.issue_id,
                       IssueEvent.action, QualityIssue.title, IssueEvent.detail).select_from(IssueEvent).join(QualityIssue).where(
                           QualityIssue.project_id == project_id)
        feed = union_all(task, issue).subquery()
        rows = session.execute(select(feed).order_by(feed.c.created_at.desc(), feed.c.id.desc())
                               .offset(offset).limit(limit)).mappings()
        items = []
        for row in rows:
            data = {key: value for key, value in row.items() if key != "detail"}
            timestamp = row.created_at
            data["created_at"] = (timestamp.astimezone(timezone.utc) if timestamp.tzinfo else
                                  timestamp.replace(tzinfo=timezone.utc)).isoformat(timespec="microseconds")
            if row.kind == "task":
                data.update({key: row.detail[key] for key in ("grading_batch_id", "revision", "run_status")})
            items.append(data)
        return {"items": items, "total": session.scalar(select(func.count()).select_from(feed))}

    @router.get("/quality/trends/{project_id}")
    def trends(project_id: str, agent_version_id: str, benchmark_version_id: str, profile_version_id: str,
               days: int = Query(30, ge=1, le=365), tags_offset: int = Query(0, ge=0),
               tags_limit: int = Query(25, ge=1, le=100), session=Depends(database)):
        version = get(session, AgentVersion, agent_version_id)
        benchmark = get(session, BenchmarkVersion, benchmark_version_id)
        profile = get(session, ProfileVersion, profile_version_id)
        if (get(session, Agent, version.agent_id).project_id != project_id
                or get(session, Benchmark, benchmark.benchmark_id).project_id != project_id
                or profile.project_id != project_id):
            raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=days - 1)
        start = datetime.combine(first, time.min, timezone.utc)
        end = datetime.combine(today + timedelta(days=1), time.min, timezone.utc)
        utc_time = func.timezone("UTC", Run.created_at) if session.bind.dialect.name == "postgresql" else Run.created_at
        day = func.date(utc_time)
        rows = session.execute(select(day, Trial.case_id, func.count(),
            func.sum(case((TrialAssessment.verdict == "passed", 1), else_=0)),
            func.sum(case((TrialAssessment.verdict == "failed", 1), else_=0)))
            .select_from(Run).join(GradingBatch, and_(GradingBatch.run_id == Run.id, GradingBatch.revision == 0))
            .join(Trial, Trial.run_id == Run.id)
            .outerjoin(TrialAssessment, and_(TrialAssessment.trial_id == Trial.id, TrialAssessment.batch_id == GradingBatch.id))
            .where(Run.project_id == project_id, Run.agent_version_id == agent_version_id,
                   Run.benchmark_version_id == benchmark_version_id, Run.profile_version_id == profile_version_id,
                   GradingBatch.status == "completed", Run.created_at >= start, Run.created_at < end)
            .group_by(day, Trial.case_id))
        daily, cases = defaultdict(Counter), defaultdict(Counter)
        for date, case_id, planned, passed, failed in rows:
            counts = dict(planned=planned, passed=passed, failed=failed)
            daily[str(date)].update(counts)
            cases[case_id].update(counts)
        tags, covered = Counter(), Counter()
        for case_id, definition in session.execute(select(TestCase.id, TestCase.definition)
                .where(TestCase.benchmark_version_id == benchmark_version_id)):
            for tag in set(definition.get("tags", [])):
                tags[tag] += 1
                covered[tag] += int(bool(cases[case_id]["passed"] + cases[case_id]["failed"]))
        series = []
        for index in range(days):
            date = (first + timedelta(days=index)).isoformat()
            counts = daily[date]
            series.append({"date": date, **trial_metrics(counts["planned"], counts["passed"], counts["failed"])})
        return {"series": series, "tags_total": len(tags),
                "tags": [{"label": tag, "cases": tags[tag], "decided_cases": covered[tag],
                          "coverage": covered[tag] / tags[tag]}
                         for tag in sorted(tags)[tags_offset:tags_offset + tags_limit]]}
