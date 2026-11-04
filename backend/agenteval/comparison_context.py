"""Readable objects alongside immutable comparison reports; never rewrite snapshots."""
from sqlalchemy import select
from agenteval.db import Agent, AgentVersion, Benchmark, BenchmarkVersion, GradingBatch, Run


def views(session, rows, serialize):
    ids = {r.report.get(f"{side}_batch_id") for r in rows for side in ("baseline", "candidate")}
    query = select(GradingBatch, Run, Agent, AgentVersion, Benchmark, BenchmarkVersion).join(Run, Run.id == GradingBatch.run_id).join(
        AgentVersion, AgentVersion.id == Run.agent_version_id).join(Agent, Agent.id == AgentVersion.agent_id).join(
        BenchmarkVersion, BenchmarkVersion.id == Run.benchmark_version_id).join(Benchmark, Benchmark.id == BenchmarkVersion.benchmark_id).where(GradingBatch.id.in_(ids))
    context = {b.id: {"agent_id": a.id, "agent_name": a.name, "agent_version_id": av.id, "agent_version": av.version,
        "benchmark_id": bm.id, "benchmark_name": bm.name, "benchmark_version_id": bv.id, "benchmark_version": bv.version,
        "run_id": r.id, "run_created_at": r.created_at, "batch_id": b.id, "batch_revision": b.revision, "batch_created_at": b.created_at}
        for b, r, a, av, bm, bv in session.execute(query)} if ids else {}
    return [{**serialize(r), "context": {side: context.get(r.report.get(f"{side}_batch_id")) for side in ("baseline", "candidate")}} for r in rows]
