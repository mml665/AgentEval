from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Entity(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Project(Entity):
    __tablename__ = "projects"
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")


class Agent(Entity):
    __tablename__ = "agents"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str]
    description: Mapped[str]
    adapter_type: Mapped[str]


class AgentVersion(Entity):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    version: Mapped[str]
    configuration: Mapped[dict] = mapped_column(JSON)
    capabilities: Mapped[list] = mapped_column(JSON)
    content_hash: Mapped[str]
    effects: Mapped[str] = mapped_column(default="unknown", server_default="unknown")


class Benchmark(Entity):
    __tablename__ = "benchmarks"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str]
    description: Mapped[str]


class BenchmarkVersion(Entity):
    __tablename__ = "benchmark_versions"
    __table_args__ = (UniqueConstraint("benchmark_id", "version"),)
    benchmark_id: Mapped[str] = mapped_column(ForeignKey("benchmarks.id"))
    version: Mapped[str]
    content_hash: Mapped[str]
    case_count: Mapped[int]


class TestCase(Entity):
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("benchmark_version_id", "case_key"),)
    benchmark_version_id: Mapped[str] = mapped_column(ForeignKey("benchmark_versions.id"), index=True)
    case_key: Mapped[str]
    definition: Mapped[dict] = mapped_column(JSON)


class ProfileVersion(Entity):
    __tablename__ = "profile_versions"
    __table_args__ = (UniqueConstraint("project_id", "name", "version"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str]
    version: Mapped[str]
    definition: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str]


class Run(Entity):
    __tablename__ = "evaluation_runs"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    benchmark_version_id: Mapped[str] = mapped_column(ForeignKey("benchmark_versions.id"))
    profile_version_id: Mapped[str] = mapped_column(ForeignKey("profile_versions.id"))
    status: Mapped[str] = mapped_column(default="pending")
    manifest: Mapped[dict] = mapped_column(JSON)
    manifest_hash: Mapped[str]
    cancel_requested: Mapped[bool] = mapped_column(default=False)


class Trial(Entity):
    __tablename__ = "trials"
    __table_args__ = (UniqueConstraint("run_id", "case_id", "repetition_index"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"))
    repetition_index: Mapped[int]


class Execution(Entity):
    __tablename__ = "case_executions"
    __table_args__ = (UniqueConstraint("trial_id", "attempt_index"),)
    trial_id: Mapped[str] = mapped_column(ForeignKey("trials.id"), index=True)
    attempt_index: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="pending")
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    trace_id: Mapped[str] = mapped_column(default=uid, index=True)
    trace_completeness: Mapped[str] = mapped_column(default="unavailable", server_default="unavailable")
    late_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    environment_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class GradingBatch(Entity):
    __tablename__ = "grading_batches"
    __table_args__ = (UniqueConstraint("run_id", "revision"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    revision: Mapped[int]
    status: Mapped[str] = mapped_column(default="pending")
    grading_manifest: Mapped[dict] = mapped_column(JSON)


class EvaluationResult(Entity):
    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("batch_id", "execution_id", "instance_id"),)
    batch_id: Mapped[str] = mapped_column(ForeignKey("grading_batches.id"), index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("case_executions.id"))
    instance_id: Mapped[str]
    result: Mapped[dict] = mapped_column(JSON)


class TrialAssessment(Entity):
    __tablename__ = "trial_assessments"
    __table_args__ = (UniqueConstraint("trial_id", "batch_id"),)
    trial_id: Mapped[str] = mapped_column(ForeignKey("trials.id"))
    batch_id: Mapped[str] = mapped_column(ForeignKey("grading_batches.id"), index=True)
    selected_execution_id: Mapped[str] = mapped_column(ForeignKey("case_executions.id"))
    verdict: Mapped[str]
    reasons: Mapped[list] = mapped_column(JSON)


class Comparison(Entity):
    __tablename__ = "regression_reports"
    report: Mapped[dict] = mapped_column(JSON)


class RequestDedup(Entity):
    __tablename__ = "request_dedup"
    __table_args__ = (UniqueConstraint("project_id", "key_hash"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    key_hash: Mapped[str]
    request_hash: Mapped[str]
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))


class WorkItem(Entity):
    """Transactional outbox and fenced work lease; queue payload contains only id."""
    __tablename__ = "work_items"
    __table_args__ = (UniqueConstraint("kind", "target_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    kind: Mapped[str]
    target_id: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending", index=True)
    owner: Mapped[str | None] = mapped_column(nullable=True)
    epoch: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[float] = mapped_column(default=0)
    next_delivery: Mapped[float] = mapped_column(default=0, index=True)
    delivery_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(nullable=True)


class AuditEvent(Entity):
    __tablename__ = "audit_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    action: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSON)


class JudgeCall(Entity):
    __tablename__ = "judge_calls"
    __table_args__ = (UniqueConstraint("batch_id", "execution_id", "instance_id", "attempt_index"),)
    batch_id: Mapped[str] = mapped_column(ForeignKey("grading_batches.id"), index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("case_executions.id"))
    instance_id: Mapped[str]
    attempt_index: Mapped[int]
    status: Mapped[str]
    model: Mapped[str]
    prompt_version: Mapped[str]
    input_hash: Mapped[str]
    response_hash: Mapped[str | None] = mapped_column(nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class JudgeBudget(Entity):
    __tablename__ = "judge_budgets"
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), unique=True)
    max_calls: Mapped[int]
    policy_source: Mapped[str]


class TraceSpan(Entity):
    __tablename__ = "trace_spans"
    __table_args__ = (UniqueConstraint("execution_id", "span_id"),)
    execution_id: Mapped[str] = mapped_column(ForeignKey("case_executions.id"), index=True)
    trace_id: Mapped[str] = mapped_column(index=True)
    span_id: Mapped[str]
    parent_span_id: Mapped[str | None] = mapped_column(nullable=True)
    record: Mapped[dict] = mapped_column(JSON)


class ServiceHeartbeat(Entity):
    __tablename__ = "service_heartbeats"
    instance_id: Mapped[str] = mapped_column(unique=True)
    role: Mapped[str] = mapped_column(index=True)
    state: Mapped[str]
    last_seen: Mapped[float] = mapped_column(index=True)
    last_success: Mapped[float]
    failures: Mapped[int]
    error_code: Mapped[str | None] = mapped_column(nullable=True)


def make_database(url: str):
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_pragmas(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine, sessionmaker(engine, expire_on_commit=False)

