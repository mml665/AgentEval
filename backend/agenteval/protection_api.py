"""Policy management, explicit raw-reading grants and evidence inventory."""
from datetime import datetime, timezone, timedelta
from fastapi import Depends, HTTPException, Request
from pydantic import Field, field_validator
from sqlalchemy import select, func, cast, String
from agenteval.contracts import Contract, digest
from agenteval.db import (CredentialVersion, EvidenceGrant, EvaluationResult, Execution, GradingBatch, Project,
    ProjectMembership, ProtectionPolicy, ReleaseDecision, Run, SecurityAudit, TeamUser, TraceSpan, Trial, TestCase)
from agenteval.security import lock_security


class PolicyChange(Contract):
    expected_revision: int = Field(default=0, ge=0)
    paths: list[str] = Field(default_factory=list, max_length=30)
    secret_env_names: list[str] = Field(default_factory=list, max_length=30)
    redact_emails: bool = True
    retention_days: int | None = Field(default=None, ge=1, le=36500)

    @field_validator("paths")
    @classmethod
    def paths_valid(cls, values):
        if any(not v.startswith("/") or len(v) > 256 or len(v.split("/")) > 30 for v in values): raise ValueError("Use bounded JSON Pointer paths with * or **")
        return sorted(set(values))

    @field_validator("secret_env_names")
    @classmethod
    def env_valid(cls, values):
        import re
        if any(not re.fullmatch(r"AGENTEVAL_REDACTION_SECRET_[A-Z0-9_]{1,100}", v) for v in values): raise ValueError("Only deployment redaction secret references are accepted")
        return sorted(set(values))


class RawGrant(Contract):
    user_id: str
    raw_read: bool


class RawRequest(Contract):
    reason: str = Field(min_length=8, max_length=500)
    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 8: raise ValueError("A meaningful reason is required")
        return value.strip()


def original_bundle(session, run):
    trials = list(session.scalars(select(Trial).where(Trial.run_id == run.id)))
    executions = list(session.scalars(select(Execution).where(Execution.trial_id.in_([r.id for r in trials]))))
    batches = list(session.scalars(select(GradingBatch).where(GradingBatch.run_id == run.id)))
    def rows(model, condition):
        return [{c.name: getattr(row, c.name) for c in model.__table__.columns} for row in session.scalars(select(model).where(condition).order_by(model.id))]
    return {"run": rows(Run, Run.id == run.id)[0], "cases": rows(TestCase, TestCase.id.in_([r.case_id for r in trials])),
        "batches": rows(GradingBatch, GradingBatch.run_id == run.id), "trials": rows(Trial, Trial.run_id == run.id),
        "executions": rows(Execution, Execution.id.in_([r.id for r in executions])),
        "spans": rows(TraceSpan, TraceSpan.execution_id.in_([r.id for r in executions])),
        "results": rows(EvaluationResult, EvaluationResult.batch_id.in_([r.id for r in batches]))}


def install(router, settings, sessions, database, get, serialize):
    @router.get("/protection/policy/{project_id}")
    def policy(project_id: str, session=Depends(database)):
        get(session, Project, project_id)
        row = session.scalar(select(ProtectionPolicy).where(ProtectionPolicy.project_id == project_id).order_by(ProtectionPolicy.revision.desc()).limit(1))
        definition = {k:v for k,v in row.definition.items() if k != "secret_fingerprints"} if row else PolicyChange().model_dump(exclude={"expected_revision"})
        return {"id": row.id if row else None, "revision": row.revision if row else 0, "definition": definition}

    @router.post("/protection/policy/{project_id}", status_code=201)
    def update_policy(project_id: str, body: PolicyChange):
        import os
        if any(not os.getenv(name) for name in body.secret_env_names): raise HTTPException(422, detail={"code": "REDACTION_SECRET_UNAVAILABLE"})
        with sessions() as session:
            lock_security(session); get(session, Project, project_id)
            revision = session.scalar(select(func.max(ProtectionPolicy.revision)).where(ProtectionPolicy.project_id == project_id)) or 0
            if revision != body.expected_revision: raise HTTPException(409, detail={"code": "PROTECTION_REVISION_CONFLICT"})
            from agenteval.credentials import fingerprint, CredentialError
            from agenteval.db import uid
            row = ProtectionPolicy(id=uid(), project_id=project_id, revision=revision+1, definition=body.model_dump(exclude={"expected_revision"}))
            try: hashes = {name: fingerprint(os.getenv(name), settings.credential_fingerprint_key, row.id) for name in body.secret_env_names}
            except CredentialError: raise HTTPException(422, detail={"code": "CREDENTIAL_PROVIDER_NOT_CONFIGURED"}) from None
            row.definition = {**row.definition, "secret_fingerprints": hashes}
            session.add(row); session.commit(); return {"id": row.id, "revision": row.revision, "project_id": project_id}

    @router.get("/protection/grants/{project_id}")
    def grants(project_id: str, request: Request, session=Depends(database)):
        principal = request.state.principal
        query = select(EvidenceGrant).where(EvidenceGrant.project_id == project_id)
        if not principal.admin: query = query.where(EvidenceGrant.user_id == principal.id)
        return {"items": [serialize(r) for r in session.scalars(query)], "personal_admin_raw_read": principal.kind == "user" and principal.admin}

    @router.post("/protection/grants/{project_id}")
    def grant(project_id: str, body: RawGrant):
        with sessions() as session:
            lock_security(session); get(session, Project, project_id)
            member = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == body.user_id))
            user = get(session, TeamUser, body.user_id)
            if not member or not user.active: raise HTTPException(422, detail={"code": "ASSIGNEE_NOT_ACTIVE_MEMBER"})
            row = session.scalar(select(EvidenceGrant).where(EvidenceGrant.project_id == project_id, EvidenceGrant.user_id == body.user_id))
            if row: row.raw_read = body.raw_read
            else: row = EvidenceGrant(project_id=project_id, user_id=body.user_id, raw_read=body.raw_read); session.add(row)
            session.commit(); return serialize(row)

    @router.post("/protection/evaluations/{run_id}/raw")
    def raw(run_id: str, body: RawRequest, request: Request, session=Depends(database)):
        principal = request.state.principal; run = get(session, Run, run_id)
        grant = session.scalar(select(EvidenceGrant).where(EvidenceGrant.project_id == run.project_id, EvidenceGrant.user_id == principal.id, EvidenceGrant.raw_read.is_(True)))
        if principal.kind != "user" or not (principal.admin or grant): raise HTTPException(403, detail={"code": "RAW_EVIDENCE_PERMISSION_REQUIRED"})
        # Commit the disclosure intent before retrieving/serializing any original payload.
        audit = SecurityAudit(actor=principal.actor(), action="RAW_EVIDENCE_READ", project_ids=[run.project_id],
            resource_ids={"run_id": run.id, "reason": body.reason, "request_audit_id": request.state.audit_id}, status=200)
        session.add(audit); session.commit()
        from fastapi.encoders import jsonable_encoder
        bundle = jsonable_encoder(original_bundle(session, run))
        request.state.raw_evidence_authorized = True
        return {"view": {"kind": "raw-audited", "audit_id": audit.id, "content_hash": digest(bundle)}, "evidence": bundle}

    @router.get("/protection/storage/{project_id}")
    def storage(project_id: str, session=Depends(database)):
        get(session, Project, project_id)
        row = session.scalar(select(ProtectionPolicy).where(ProtectionPolicy.project_id == project_id).order_by(ProtectionPolicy.revision.desc()).limit(1))
        days = row.definition.get("retention_days") if row else None
        runs = list(session.scalars(select(Run).where(Run.project_id == project_id)))
        referenced = set()
        for decision in session.scalars(select(ReleaseDecision).where(ReleaseDecision.project_id == project_id)):
            for side in ("baseline", "candidate"):
                referenced.add(decision.snapshot["comparison"][side]["run_id"])
        old = [r.id for r in runs if days is not None and r.created_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)-timedelta(days=days)]
        execution_ids = select(Execution.id).join(Trial).join(Run).where(Run.project_id == project_id)
        characters = session.scalar(select(func.coalesce(func.sum(func.length(cast(Execution.response, String))), 0)).where(Execution.id.in_(execution_ids)))
        characters += session.scalar(select(func.coalesce(func.sum(func.length(cast(TraceSpan.record, String))), 0)).where(TraceSpan.execution_id.in_(execution_ids)))
        return {"project_id": project_id, "runs": len(runs), "release_referenced_runs": len(referenced), "retention_days": days,
            "past_retention_runs": len(old), "past_retention_release_holds": len(set(old) & referenced),
            "response_and_trace_json_characters": characters, "capacity_scope": "Serialized response and trace character count; not physical disk bytes or all evidence tables.",
            "automatic_deletion": False, "archive_mode": "non_destructive_project_snapshot", "scope": "Age is advisory; fixed evidence and original rows are retained."}
