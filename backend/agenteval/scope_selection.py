"""Bounded selector options and direct lookup of pinned run and batch identities."""
from typing import Literal

from fastapi import Depends, HTTPException, Query
from sqlalchemy import case, func, select

from agenteval.db import Agent, AgentVersion, BaselineSetting, Benchmark, BenchmarkVersion, GradingBatch, Project, Run


OptionKind = Literal["agent", "version", "benchmark", "run", "batch"]


def run_options(project_id):
    return select(Run.id).join(AgentVersion).where(Run.project_id == project_id)


def options(session, project_id, kind, agent_id=None, agent_version_id=None, benchmark_version_id=None,
            run_id=None, offset=0, limit=50, settled=False):
    if kind == "batch":
        query = (select(GradingBatch.id, GradingBatch.revision, GradingBatch.run_id)
            .join(Run).where(Run.project_id == project_id, Run.id == run_id, GradingBatch.status == "completed")
            .order_by(GradingBatch.revision.desc(), GradingBatch.id))
    else:
        if kind == "agent":
            columns = (Agent.id, Agent.name)
        elif kind == "version":
            columns = (AgentVersion.id, Agent.name, AgentVersion.version)
        elif kind == "benchmark":
            columns = (BenchmarkVersion.id, Benchmark.name, BenchmarkVersion.version)
        else:
            columns = (Run.id, Agent.name, AgentVersion.version, Benchmark.name, BenchmarkVersion.version)
        query = (select(*columns).select_from(Run).join(AgentVersion).join(Agent).join(BenchmarkVersion, Run.benchmark_version_id == BenchmarkVersion.id)
            .join(Benchmark).where(Run.project_id == project_id))
        if agent_id: query = query.where(Agent.id == agent_id)
        if agent_version_id: query = query.where(Run.agent_version_id == agent_version_id)
        if benchmark_version_id: query = query.where(Run.benchmark_version_id == benchmark_version_id)
        if kind == "run":
            if settled: query = query.where(Run.status.in_(["completed", "cancelled"]))
            query = query.order_by(Run.created_at.desc(), Run.id)
        else:
            query = query.group_by(*columns).order_by(func.max(Run.created_at).desc(), columns[0])
    rows = session.execute(query.offset(offset).limit(limit + 1)).all()
    items = []
    for identifier, *parts in rows[:limit]:
        if kind == "batch":
            label = batch_label(identifier, *parts)
        elif kind == "agent":
            label = parts[0]
        else:
            label = f"{parts[0]} · v{parts[1]}"
            if kind == "run": label += f" · {parts[2]} v{parts[3]}"
            if kind in ("benchmark", "run"): label += f" · {identifier[:8]}"
        items.append({"id": identifier, "label": label})
    return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}


def batch_label(identifier, revision, run_id):
    return f"运行 {run_id[:8]} · 第 {revision + 1} 批 · {identifier[:8]}"


def mismatch():
    return HTTPException(422, detail={"code": "EVALUATION_SCOPE_MISMATCH"})


def choose_side(session, get, project_id, agent_id, version_id, benchmark_id, run_id, batch_id):
    batch = get(session, GradingBatch, batch_id) if batch_id else None
    if batch and run_id and batch.run_id != run_id:
        raise mismatch()
    pinned = run_id or (batch.run_id if batch else None)
    query = run_options(project_id)
    if agent_id: query = query.where(AgentVersion.agent_id == agent_id)
    if version_id: query = query.where(Run.agent_version_id == version_id)
    if benchmark_id: query = query.where(Run.benchmark_version_id == benchmark_id)
    if pinned: query = query.where(Run.id == pinned)
    chosen = session.scalar(query.order_by(case((Run.status.in_(["completed", "cancelled"]), 0), else_=1),
        Run.created_at.desc(), Run.id).limit(1))
    if not chosen:
        if any((agent_id, version_id, benchmark_id, pinned)): raise mismatch()
        return None, None
    if not batch:
        batch = session.scalar(select(GradingBatch).where(GradingBatch.run_id == chosen, GradingBatch.status == "completed")
            .order_by(GradingBatch.revision.desc(), GradingBatch.id).limit(1))
    return get(session, Run, chosen), batch


def install(router, database, get):
    @router.get("/quality/scope-options/{project_id}")
    def get_options(project_id: str, kind: OptionKind, agent_id: str | None = None, agent_version_id: str | None = None,
        benchmark_version_id: str | None = None, run_id: str | None = None, offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100), settled: bool = False, session=Depends(database)):
        get(session, Project, project_id)
        return options(session, project_id, kind, agent_id, agent_version_id, benchmark_version_id, run_id, offset, limit, settled)

    @router.get("/quality/scope-selection/{project_id}")
    def selection(project_id: str, agent_id: str | None = None, candidate_version_id: str | None = None,
        baseline_version_id: str | None = None, benchmark_version_id: str | None = None,
        candidate_run_id: str | None = None, baseline_run_id: str | None = None,
        candidate_batch_id: str | None = None, baseline_batch_id: str | None = None, session=Depends(database)):
        get(session, Project, project_id)
        candidate, cb = choose_side(session, get, project_id, agent_id, candidate_version_id,
            benchmark_version_id, candidate_run_id, candidate_batch_id)
        if not candidate:
            if baseline_version_id or baseline_batch_id or baseline_run_id: raise mismatch()
            return {"selection": None, "options": {}}
        cv = get(session, AgentVersion, candidate.agent_version_id)
        agent = get(session, Agent, cv.agent_id)
        bv = get(session, BenchmarkVersion, candidate.benchmark_version_id)
        benchmark = get(session, Benchmark, bv.benchmark_id)
        saved = session.scalar(select(BaselineSetting).where(BaselineSetting.project_id == project_id,
            BaselineSetting.agent_id == agent.id, BaselineSetting.benchmark_version_id == bv.id))
        baseline, bb = None, None
        if baseline_version_id or baseline_run_id or baseline_batch_id:
            baseline, bb = choose_side(session, get, project_id, agent.id, baseline_version_id, bv.id, baseline_run_id, baseline_batch_id)
        elif baseline_version_id is None and saved:
            baseline, bb = choose_side(session, get, project_id, agent.id, None, bv.id, None, saved.grading_batch_id)
        version = get(session, AgentVersion, baseline.agent_version_id) if baseline else None
        return {
            "selection": {"agent_id": agent.id, "candidate_version_id": cv.id, "benchmark_version_id": bv.id,
                "candidate_run_id": candidate.id, "candidate_batch_id": cb.id if cb else "",
                "baseline_version_id": version.id if version else "", "baseline_run_id": baseline.id if baseline else "",
                "baseline_batch_id": bb.id if bb else ""},
            "candidate_run_status": candidate.status,
            "candidate_batch_status": cb.status if cb else None, "baseline_batch_status": bb.status if bb else None,
            "baseline_revision": saved.revision if saved else 0,
            "labels": {"agent_id": agent.name, "candidate_version_id": f"{agent.name} · v{cv.version}",
                "baseline_version_id": f"{agent.name} · v{version.version}" if version else "",
                "benchmark_version_id": f"{benchmark.name} · v{bv.version} · {bv.id[:8]}",
                "candidate_batch_id": batch_label(cb.id, cb.revision, candidate.id) if cb else "",
                "baseline_batch_id": batch_label(bb.id, bb.revision, baseline.id) if bb else ""},
            "options": {"agent_id": options(session, project_id, "agent"),
                "candidate_version_id": options(session, project_id, "version", agent_id=agent.id),
                "baseline_version_id": options(session, project_id, "version", agent_id=agent.id, benchmark_version_id=bv.id),
                "benchmark_version_id": options(session, project_id, "benchmark", agent_version_id=cv.id)},
        }
