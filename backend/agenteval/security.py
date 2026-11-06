"""Database-backed identities and deny-by-default project authorization."""
from contextvars import ContextVar
from dataclasses import dataclass
import asyncio
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from agenteval.db import (Agent, AgentVersion, AuditEvent, Benchmark, BenchmarkVersion, Comparison, CredentialVersion,
    Execution, GradingBatch, IssueEvent, MachineToken, ProfileVersion, Project, ProjectMembership,
    QualityIssue, ReleaseDecision, Run, SecurityAudit, SecurityLock, TeamUser, TestCase, Trial, UserSession)

actor_context = ContextVar("http_actor", default=None)
MUTATING_EVENTS = {"developer_update", "scope_changed", "bulk_confirm", "bulk_ignore", "bulk_merge", "bulk_assign",
    "retest_started", "retest", "promoted_to_regression"}
SCOPED_ROUTES = {
    "/quality/scope-options/{project_id}", "/quality/scope-selection/{project_id}",
    "/workspace-versions/{project_id}",
    "/onboarding/preview", "/onboarding/reuse-preview", "/onboarding/reuse", "/onboarding/requests/{request_key}", "/onboarding/drafts/{project_id}",
    "/evaluations/{run_id}/reuse-config", "/benchmarks/{benchmark_id}/preview", "/benchmarks/{benchmark_id}/publish", "/benchmark-versions/{version_id}/diff", "/quality/baselines/{project_id}",
    "/protection/policy/{project_id}", "/protection/grants/{project_id}", "/protection/storage/{project_id}", "/protection/evaluations/{run_id}/raw",
    "/agents", "/agents/{agent_id}/versions", "/benchmarks", "/benchmarks/{benchmark_id}/versions", "/benchmark-versions/{version_id}/cases",
    "/profile-versions", "/evaluations", "/evaluations/{run_id}", "/evaluations/{run_id}/cancel", "/evaluations/{run_id}/regrade",
    "/evaluations/{run_id}/report", "/evaluations/{run_id}/results", "/evaluations/{run_id}/bad-cases", "/evaluations/{run_id}/work-items",
    "/evaluations/{run_id}/audit", "/evaluations/{run_id}/judge-calls", "/evaluations/{run_id}/judge-budget", "/evaluations/{run_id}/calibration",
    "/evaluations/{run_id}/export", "/executions/{execution_id}", "/executions/{execution_id}/trace", "/executions/{execution_id}/evidence-summary",
    "/traces/{trace_id}", "/regressions", "/regressions/compare", "/regressions/{comparison_id}", "/regressions/{comparison_id}/export",
    "/workspace/{project_id}", "/component-evaluations", "/onboarding/check", "/onboarding/evaluations", "/onboarding/test-call",
    "/quality/batches", "/quality/agent-versions", "/quality/runs", "/quality/comparisons", "/quality/issues", "/quality/issues/import",
    "/quality/issues/bulk", "/quality/issues/{issue_id}", "/quality/issues/{issue_id}/update", "/quality/issues/{issue_id}/scope",
    "/quality/issues/{issue_id}/retest", "/quality/issues/{issue_id}/evaluations", "/quality/issues/{issue_id}/regression-case",
    "/quality/policy/{project_id}", "/quality/workbench/{project_id}", "/quality/project-overview/{project_id}",
    "/release-decisions", "/release-decisions/{decision_id}", "/release-decisions/{decision_id}/export", "/team/members", "/team/credentials",
}
SELF_ROUTES = {"/auth/sessions", "/auth/sessions/{session_id}/revoke"}


@event.listens_for(Session, "before_flush")
def attribute_changes(session, *_):
    actor = actor_context.get()
    if not actor: return
    for row in session.new:
        if isinstance(row, Run) and row.initiator is None: row.initiator = dict(actor)
        elif isinstance(row, ReleaseDecision) and row.actor is None: row.actor = dict(actor)
        elif isinstance(row, IssueEvent) and row.action in MUTATING_EVENTS and row.actor is None: row.actor = dict(actor)
        elif isinstance(row, AuditEvent) and row.action == "ADMIN_CAPACITY_RELEASED":
            row.detail = {**row.detail, "actor": dict(actor)}


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


def assign_member(session, issue, user_id, owner=""):
    actor = actor_context.get() or {}
    if not user_id and actor.get("kind") == "legacy_team":
        issue.owner, issue.assignee_user_id = owner, None
        return
    if not user_id:
        if owner: raise HTTPException(422, detail={"code": "ASSIGNEE_REFERENCE_REQUIRED"})
        issue.owner, issue.assignee_user_id = "", None
        return
    user = session.get(TeamUser, user_id)
    member = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == issue.project_id, ProjectMembership.user_id == user_id))
    if not user or not user.active or not member: raise HTTPException(422, detail={"code": "ASSIGNEE_NOT_ACTIVE_MEMBER"})
    if issue.owner and not issue.assignee_user_id and issue.legacy_owner is None: issue.legacy_owner = issue.owner
    issue.owner, issue.assignee_user_id = user.display_name, user.id


def hash_password(password):
    # OWASP scrypt profile: N=2^14, r=8, p=5 (16 MiB). No third-party password serializer.
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=5, maxmem=64*1024*1024)
    return "scrypt-14-8-5$" + salt.hex() + "$" + value.hex()


def verify_password(password, stored):
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt-14-8-5": return False
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=5, maxmem=64*1024*1024)
        return hmac.compare_digest(value.hex(), expected)
    except (ValueError, TypeError): return False


def lock_security(session):
    if session.bind.dialect.name == "sqlite": session.execute(text("BEGIN IMMEDIATE"))
    else: session.scalar(select(SecurityLock).where(SecurityLock.name == "security").with_for_update())


def bootstrap_admin(sessions, username, password, display_name="Administrator"):
    from agenteval.team_contracts import NewUser
    body = NewUser(username=username, password=password, display_name=display_name, is_admin=True)
    with sessions() as session:
        lock_security(session)
        if session.scalar(select(TeamUser.id).limit(1)):
            raise ValueError("Administrator initialization is one-time; use an existing administrator")
        if not session.scalar(select(SecurityLock).where(SecurityLock.name == "security")):
            session.add(SecurityLock(name="security"))
        user = TeamUser(username=body.username, display_name=body.display_name, password_hash=hash_password(body.password), is_admin=True)
        session.add(user); session.flush()
        session.add(SecurityAudit(actor={"kind": "deployment_cli", "id": None, "display_name": "Deployment initializer"},
            action="BOOTSTRAP_ADMIN", project_ids=[], resource_ids={"user_id": user.id}, status=201))
        session.commit()
        return user.id


@dataclass
class Principal:
    kind: str
    id: str | None
    display_name: str
    admin: bool
    roles: dict
    session_id: str | None = None
    csrf_token: str = ""

    def actor(self): return {"kind": self.kind, "id": self.id, "display_name": self.display_name}
    def view(self): return {**self.actor(), "is_admin": self.admin, "project_roles": self.roles,
        "session_id": self.session_id, "csrf_token": self.csrf_token, "legacy": self.kind == "legacy_team"}


class Security:
    def __init__(self, settings, sessions): self.settings, self.sessions = settings, sessions

    @property
    def legacy_enabled(self): return self.settings.auth_mode == "legacy" or self.settings.legacy_token_enabled

    @property
    def cookie_name(self): return "__Host-agenteval" if self.settings.session_cookie_secure else "agenteval_session"

    def origin(self, request):
        if request.headers.get("origin") not in self.settings.session_origins:
            raise HTTPException(403, detail={"code": "ORIGIN_NOT_ALLOWED"})

    def authenticate(self, request):
        header = request.headers.get("authorization")
        with self.sessions() as session:
            if header is not None:
                if not header.startswith("Bearer ") or len(header) > 1024: raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
                token = header[7:]
                if self.legacy_enabled and self.settings.api_token and secrets.compare_digest(token.encode(), self.settings.api_token.encode()):
                    return Principal("legacy_team", None, "历史共享团队身份", True, {})
                machine = session.scalar(select(MachineToken).where(MachineToken.token_digest == digest(token)))
                if not machine or machine.revoked or machine.expires_at <= time.time(): raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
                return Principal("machine", machine.id, machine.name, False,
                    {p: "viewer" if machine.permission == "read" else "evaluator" for p in machine.project_ids})
            raw = request.cookies.get(self.cookie_name, "")
            value = session.scalar(select(UserSession).where(UserSession.token_digest == digest(raw))) if raw and len(raw) <= 256 else None
            now = time.time()
            if not value or value.revoked or value.expires_at <= now or value.last_seen + self.settings.session_idle_seconds <= now:
                raise HTTPException(401, detail={"code": "SESSION_EXPIRED"})
            user = session.get(TeamUser, value.user_id)
            if not user or not user.active: raise HTTPException(401, detail={"code": "SESSION_REVOKED"})
            roles = {r.project_id: r.role for r in session.scalars(select(ProjectMembership).where(ProjectMembership.user_id == user.id))}
            value.last_seen = now; session.commit()
            return Principal("user", user.id, user.display_name, user.is_admin, roles, value.id, value.csrf_token)

    def project_for(self, session, model, key):
        if not isinstance(key, str) or len(key) > 128: raise HTTPException(422, detail={"code": "INVALID_RESOURCE_ID"})
        value = session.get(model, key)
        if not value: raise HTTPException(404, detail={"code": "NOT_FOUND"})
        if model is Project: return {value.id}
        if model is AgentVersion: return self.project_for(session, Agent, value.agent_id)
        if model is BenchmarkVersion: return self.project_for(session, Benchmark, value.benchmark_id)
        if model is TestCase: return self.project_for(session, BenchmarkVersion, value.benchmark_version_id)
        if model is GradingBatch: return self.project_for(session, Run, value.run_id)
        if model is Execution: return self.project_for(session, Trial, value.trial_id)
        if model is Trial: return self.project_for(session, Run, value.run_id)
        if model is Comparison:
            return set().union(*(self.project_for(session, Run, value.report[side]["run_id"]) for side in ("baseline", "candidate")))
        if model is ReleaseDecision:
            return {value.project_id} | self.project_for(session, Comparison, value.comparison_id)
        return {value.project_id}

    async def authorize(self, request: Request):
        principal = await asyncio.to_thread(self.authenticate, request)
        request.state.principal = principal
        actor_context.set(principal.actor())
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        if write and principal.kind == "user":
            self.origin(request)
            if not secrets.compare_digest(request.headers.get("x-csrf-token", "").encode(), principal.csrf_token.encode()):
                raise HTTPException(403, detail={"code": "CSRF_REJECTED"})
        route = request.scope["route"].path.removeprefix("/api/v1")
        body = await request.json() if write and request.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(body, dict): body = {}
        public_metadata = {"/capabilities", "/evaluators", "/environments", "/components", "/onboarding/options", "/openapi.json"}
        administrative = route.startswith(("/team/users", "/team/machines", "/team/audit", "/operations/")) or route == "/projects" and write
        administrative |= route == "/quality/policy/{project_id}" and write
        administrative |= route.startswith("/team/members") and write
        administrative |= route.startswith("/team/credentials") and write
        administrative |= route.startswith(("/protection/policy/", "/protection/grants/")) and write
        administrative |= route.endswith("/release-capacity")
        if administrative and not principal.admin: raise HTTPException(403, detail={"code": "ADMIN_REQUIRED"})
        if not principal.admin and route not in SCOPED_ROUTES | SELF_ROUTES | public_metadata | {"/projects"}:
            raise HTTPException(403, detail={"code": "ADMIN_REQUIRED"})
        if route in ("/onboarding/check", "/onboarding/evaluations", "/onboarding/preview") and not body.get("project_id") and not principal.admin:
            raise HTTPException(403, detail={"code": "PROJECT_SCOPE_REQUIRED"})
        await asyncio.to_thread(self.scope, request, principal, body, write, route, public_metadata)
        from agenteval.protection import view_context
        view_context.set(request.state.protection_policy)
        if write: actor_context.set({**principal.actor(), "audit_id": request.state.audit_id})
        return principal

    def scope(self, request, principal, body, write, route, public_metadata):
        project_ids = set()
        with self.sessions() as session:
            mappings = {"project_id": Project, "agent_id": Agent, "agent_version_id": AgentVersion,
                "candidate_version_id": AgentVersion, "baseline_version_id": AgentVersion,
                "benchmark_id": Benchmark, "benchmark_version_id": BenchmarkVersion, "profile_version_id": ProfileVersion,
                "run_id": Run, "baseline_run_id": Run, "candidate_run_id": Run, "grading_batch_id": GradingBatch,
                "baseline_batch_id": GradingBatch, "candidate_batch_id": GradingBatch,
                "execution_id": Execution, "issue_id": QualityIssue, "comparison_id": Comparison, "decision_id": ReleaseDecision,
                "credential_version_id": CredentialVersion, "case_id": TestCase, "other_version_id": BenchmarkVersion, "source_run_id": Run}
            for data in (request.path_params, dict(request.query_params), body, body.get("payload") if isinstance(body.get("payload"), dict) else {}):
                for key, model in mappings.items():
                    if data.get(key):
                        if not isinstance(data[key], str) or len(data[key]) > 128: raise HTTPException(422, detail={"code": "INVALID_RESOURCE_ID"})
                        project_ids |= self.project_for(session, model, data[key])
            if route in ("/benchmark-versions/{version_id}/cases", "/benchmark-versions/{version_id}/diff"): project_ids |= self.project_for(session, BenchmarkVersion, request.path_params["version_id"])
            if route == "/traces/{trace_id}":
                execution = session.scalar(select(Execution).where(Execution.trace_id == request.path_params["trace_id"]))
                if not execution: raise HTTPException(404, detail={"code": "NOT_FOUND"})
                project_ids |= self.project_for(session, Execution, execution.id)
            for key, model in (("issue_ids", QualityIssue), ("version_ids", AgentVersion)):
                if isinstance(body.get(key), list) and len(body[key]) > (100 if key == "issue_ids" else 1000):
                    raise HTTPException(422, detail={"code": "TOO_MANY_RESOURCE_IDS"})
                for value in body.get(key, []) if isinstance(body.get(key, []), list) else []:
                    project_ids |= self.project_for(session, model, value)
            guided_agent = body.get("agent") if isinstance(body.get("agent"), dict) else {}
            configuration = body.get("configuration") or guided_agent.get("configuration") or {}
            if isinstance(configuration, dict) and configuration.get("credential_version_id"):
                project_ids |= self.project_for(session, CredentialVersion, configuration["credential_version_id"])
            if not principal.admin:
                if any(p not in principal.roles for p in project_ids): raise HTTPException(404, detail={"code": "NOT_FOUND"})
                if write and route != "/protection/evaluations/{run_id}/raw" and any(principal.roles[p] != "evaluator" for p in project_ids): raise HTTPException(403, detail={"code": "READ_ONLY"})
                if not project_ids and route not in public_metadata | {"/projects"} | SELF_ROUTES:
                    raise HTTPException(403, detail={"code": "PROJECT_SCOPE_REQUIRED"})
            from agenteval.protection import load_policy
            request.state.protection_policy = load_policy(session, self.settings, project_ids or set(principal.roles))
            if write:
                audit = SecurityAudit(actor=principal.actor(), action=request.method + " " + route,
                    project_ids=sorted(project_ids), resource_ids={k: str(v)[:100] for k, v in request.path_params.items()}, status=None)
                session.add(audit); session.commit(); request.state.audit_id = audit.id

    def finish_audit(self, audit_id, status, resource_ids=None):
        with self.sessions() as session:
            row = session.get(SecurityAudit, audit_id)
            if row:
                row.status = status
                if resource_ids: row.resource_ids = {**row.resource_ids, **resource_ids}
                session.commit()

    def rejection(self, scope, status):
        principal = scope.get("state", {}).get("principal")
        if not principal: return
        with self.sessions() as session:
            session.add(SecurityAudit(actor=principal.actor(), action="REJECTED " + scope.get("method", "") + " " + getattr(scope.get("route"), "path", "unknown"),
                project_ids=[], resource_ids={k: str(v)[:100] for k,v in scope.get("path_params", {}).items()}, status=status))
            session.commit()


class SecurityResponse:
    def __init__(self, app, security): self.app, self.security = app, security

    async def __call__(self, scope, receive, send):
        body = bytearray()
        response_status = None
        async def guarded(message):
            nonlocal response_status
            if scope["type"] == "http" and message["type"] == "http.response.start":
                response_status = message["status"]
                audit_id = scope.get("state", {}).get("audit_id")
                if audit_id: await asyncio.to_thread(self.security.finish_audit, audit_id, message["status"])
                elif message["status"] >= 400 and scope.get("state", {}).get("principal"):
                    await asyncio.to_thread(self.security.rejection, scope, message["status"])
                if scope.get("path", "").startswith("/api/v1/"):
                    message["headers"] = [(k,v) for k,v in message.get("headers", []) if k.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]
            if scope["type"] == "http" and message["type"] == "http.response.body" and scope.get("state", {}).get("audit_id"):
                if len(body) < 2_097_152: body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    import json
                    resources = {}
                    try:
                        value = json.loads(body)
                        if isinstance(value, dict) and isinstance(value.get("id"), str): resources["result_id"] = value["id"][:100]
                        if isinstance(value, dict):
                            # Audit only explicit identifiers/state, never arbitrary response data or credentials.
                            for key in ("user_id", "project_id", "session_id", "role", "permission", "active", "revoked"):
                                item = value.get(key)
                                if isinstance(item, (str, bool)): resources[key] = item[:128] if isinstance(item, str) else item
                        if isinstance(value, dict) and isinstance(value.get("items"), list):
                            resources["result_ids"] = [r["id"][:100] for r in value["items"][:100] if isinstance(r, dict) and isinstance(r.get("id"), str)]
                    except (ValueError, UnicodeError): pass
                    if resources: await asyncio.to_thread(self.security.finish_audit, scope["state"]["audit_id"], response_status, resources)
            await send(message)
        await self.app(scope, receive, guarded)
