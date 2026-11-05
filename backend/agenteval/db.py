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
    initiator: Mapped[dict | None] = mapped_column(JSON, nullable=True)


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


class QualityIssue(Entity):
    __tablename__ = "quality_issues"
    __table_args__ = (UniqueConstraint("batch_id", "execution_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("grading_batches.id"))
    execution_id: Mapped[str] = mapped_column(ForeignKey("case_executions.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"))
    title: Mapped[str]
    category: Mapped[str]
    severity: Mapped[str]
    kind: Mapped[str] = mapped_column(default="anomaly", server_default="anomaly")
    merged_into_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(default="open")
    revision: Mapped[int] = mapped_column(default=0)
    owner: Mapped[str] = mapped_column(default="")
    assignee_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    legacy_owner: Mapped[str | None] = mapped_column(nullable=True)
    confirmed_cause: Mapped[str] = mapped_column(default="")
    evidence: Mapped[dict] = mapped_column(JSON)
    retest_batch_id: Mapped[str | None] = mapped_column(ForeignKey("grading_batches.id"), nullable=True)
    regression_version_id: Mapped[str | None] = mapped_column(ForeignKey("benchmark_versions.id"), nullable=True)


class IssueScope(Entity):
    __tablename__ = "issue_scopes"
    issue_id: Mapped[str] = mapped_column(ForeignKey("quality_issues.id"), unique=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    benchmark_id: Mapped[str] = mapped_column(ForeignKey("benchmarks.id"), index=True)
    case_hash: Mapped[str]
    version_ids: Mapped[list] = mapped_column(JSON)


class QualityPolicy(Entity):
    __tablename__ = "quality_policies"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True)
    ingestion_mode: Mapped[str] = mapped_column(default="automatic")


class IssueEvent(Entity):
    __tablename__ = "issue_events"
    issue_id: Mapped[str] = mapped_column(ForeignKey("quality_issues.id"), index=True)
    action: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RegressionCollection(Entity):
    __tablename__ = "regression_collections"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True)
    benchmark_id: Mapped[str] = mapped_column(ForeignKey("benchmarks.id"))


class ReleaseDecision(Entity):
    __tablename__ = "release_decisions"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    comparison_id: Mapped[str] = mapped_column(ForeignKey("regression_reports.id"))
    status: Mapped[str]
    snapshot: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OnboardingReceipt(Entity):
    __tablename__ = "onboarding_receipts"
    request_key: Mapped[str] = mapped_column(unique=True)
    request_hash: Mapped[str]
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))


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


class TeamUser(Entity):
    __tablename__ = "team_users"
    username: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    password_hash: Mapped[str]
    is_admin: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)


class ProjectMembership(Entity):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("team_users.id"), index=True)
    role: Mapped[str]


class UserSession(Entity):
    __tablename__ = "user_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("team_users.id"), index=True)
    token_digest: Mapped[str] = mapped_column(unique=True)
    csrf_token: Mapped[str]
    expires_at: Mapped[float]
    last_seen: Mapped[float]
    revoked: Mapped[bool] = mapped_column(default=False)


class MachineToken(Entity):
    __tablename__ = "machine_tokens"
    name: Mapped[str]
    token_digest: Mapped[str] = mapped_column(unique=True)
    project_ids: Mapped[list] = mapped_column(JSON)
    permission: Mapped[str]
    expires_at: Mapped[float]
    revoked: Mapped[bool] = mapped_column(default=False)


class CredentialVersion(Entity):
    __tablename__ = "credential_versions"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str]
    env_name: Mapped[str]
    auth_mode: Mapped[str]
    header_name: Mapped[str]
    fingerprint: Mapped[str]
    revoked: Mapped[bool] = mapped_column(default=False)


class SecurityLock(Entity):
    __tablename__ = "security_locks"
    name: Mapped[str] = mapped_column(unique=True)


class LoginThrottle(Entity):
    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(unique=True)
    window_end: Mapped[float]
    attempts: Mapped[int]


class SecurityAudit(Entity):
    __tablename__ = "security_audits"
    actor: Mapped[dict] = mapped_column(JSON)
    action: Mapped[str]
    project_ids: Mapped[list] = mapped_column(JSON)
    resource_ids: Mapped[dict] = mapped_column(JSON)
    status: Mapped[int | None] = mapped_column(nullable=True)


class ProtectionPolicy(Entity):
    __tablename__ = "protection_policies"
    __table_args__ = (UniqueConstraint("project_id", "revision"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    revision: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSON)


class BaselineSetting(Entity):
    __tablename__ = "baseline_settings"
    __table_args__ = (UniqueConstraint("project_id", "agent_id", "benchmark_version_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    benchmark_version_id: Mapped[str] = mapped_column(ForeignKey("benchmark_versions.id"))
    grading_batch_id: Mapped[str] = mapped_column(ForeignKey("grading_batches.id"))
    revision: Mapped[int] = mapped_column(default=1)
    actor: Mapped[dict] = mapped_column(JSON)


class EvaluationDraft(Entity):
    __tablename__ = "evaluation_drafts"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("team_users.id"))
    revision: Mapped[int] = mapped_column(default=1)
    payload: Mapped[dict] = mapped_column(JSON)


class EvidenceGrant(Entity):
    __tablename__ = "evidence_grants"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("team_users.id"))
    raw_read: Mapped[bool] = mapped_column(default=False)


def make_database(url: str):
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
    engine = create_engine(url, hide_parameters=True, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_pragmas(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine, sessionmaker(engine, expire_on_commit=False)

