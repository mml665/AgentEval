"""Versioned environment-secret references. Plain values never enter persisted manifests."""
import hashlib
import hmac
import os
import re
from urllib.parse import quote, quote_plus
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from agenteval.db import Agent, AgentVersion, Benchmark, BenchmarkVersion, CredentialVersion, Project, TestCase
from agenteval.team_contracts import NewCredential, TestHttp

FORBIDDEN_HEADERS = {"host", "cookie", "set-cookie", "connection", "content-length", "content-type", "transfer-encoding",
    "proxy-authorization", "origin", "traceparent", "tracestate", "baggage"}


class CredentialError(Exception):
    def __init__(self, code): super().__init__(code); self.code = code


def fingerprint(secret, key, version_id):
    if len(key) < 32: raise CredentialError("CREDENTIAL_PROVIDER_NOT_CONFIGURED")
    return hmac.new(key.encode(), (version_id + "\0" + secret).encode(), hashlib.sha256).hexdigest()


def resolve(session, settings, project_id, configuration):
    reference = configuration.get("credential_version_id")
    if not reference: return {}
    row = session.get(CredentialVersion, reference)
    if not row or row.project_id != project_id: raise CredentialError("CREDENTIAL_SCOPE_MISMATCH")
    if row.revoked: raise CredentialError("CREDENTIAL_REVOKED")
    value = os.getenv(row.env_name, "")
    if not value or value != value.strip() or len(value) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in value): raise CredentialError("CREDENTIAL_UNAVAILABLE")
    if not hmac.compare_digest(row.fingerprint, fingerprint(value, settings.credential_fingerprint_key, row.id)):
        raise CredentialError("CREDENTIAL_VERSION_CHANGED")
    return {row.header_name: "Bearer " + value if row.auth_mode == "bearer" else value}


def validate_reference(session, settings, project_id, configuration):
    try: resolve(session, settings, project_id, configuration)
    except CredentialError as exc: raise HTTPException(422, detail={"code": exc.code}) from None


def contains_secret(value, headers):
    import json
    from urllib.parse import unquote, unquote_plus
    raw = json.dumps(value, ensure_ascii=False)
    values = [v[7:] if k.lower() == "authorization" and v.startswith("Bearer ") else v for k,v in headers.items()]
    decoded = [raw]
    for _ in range(3): decoded += [unquote(decoded[-1]), unquote_plus(decoded[-1])]
    return any(variant and variant in text for text in decoded for secret in values for variant in (secret, quote(secret, safe=""), quote_plus(secret), json.dumps(secret)[1:-1]))


def public(row):
    return {"id": row.id, "project_id": row.project_id, "name": row.name, "auth_mode": row.auth_mode,
        "header_name": row.header_name, "revoked": row.revoked, "created_at": row.created_at}


def install(router, settings, database, get):
    @router.post("/onboarding/test-call")
    async def test_call(body: TestHttp, session=Depends(database)):
        import asyncio
        from uuid import uuid4
        import httpx
        from agenteval.adapters import AgentFailure
        from agenteval.tracing import invoke_traced
        version = get(session, AgentVersion, body.agent_version_id)
        agent = get(session, Agent, version.agent_id)
        benchmark = get(session, BenchmarkVersion, body.benchmark_version_id)
        case = get(session, TestCase, body.case_id)
        if agent.adapter_type != "http" or case.benchmark_version_id != benchmark.id or get(session, Benchmark, benchmark.benchmark_id).project_id != agent.project_id:
            raise HTTPException(422, detail={"code": "TEST_CALL_SCOPE_MISMATCH"})
        validate_reference(session, settings, agent.project_id, version.configuration)
        call_id = str(uuid4())
        capture = {"_http_headers": resolve(session, settings, agent.project_id, version.configuration)}
        request = {"run_id": call_id, "trial_id": call_id, "execution_id": call_id, "session_id": call_id,
            "input": case.definition["input"], "case_key": case.case_key, "trace_enabled": True}
        try:
            async with asyncio.timeout(body.timeout_seconds):
                result = await invoke_traced("http", version.configuration, request, body.timeout_seconds, settings, capture)
        except AgentFailure as exc:
            return {"status": "failed", "code": exc.code, "checks": {"address": "verified", "protocol": "not_verified"}, "stored_as_evaluation": False}
        except (TimeoutError, httpx.TimeoutException):
            return {"status": "unknown", "code": "HTTP_TEST_TIMEOUT", "remote_outcome": "unknown", "stored_as_evaluation": False}
        except httpx.RequestError:
            return {"status": "unknown", "code": "HTTP_CONNECTION_FAILED", "remote_outcome": "unknown", "stored_as_evaluation": False}
        observations = result.get("observations", {})
        if not isinstance(observations, dict): observations = {}
        missing = [c for c in version.capabilities if c not in result.get("observations_complete", []) or not isinstance(observations.get(c), list)]
        return {"status": "protocol_verified" if not missing else "missing_observations", "missing_channels": missing,
            "checks": {"address": "verified", "network": "reachable", "authentication": "request_accepted" if capture["_http_headers"] else "not_configured", "protocol": "verified", "observations": "missing" if missing else "declared_channels_present" if version.capabilities else "not_declared"},
            "result": result, "trace": capture.get("spans", []), "stored_as_evaluation": False,
            "scope": "Explicit test call in a user-confirmed test environment; not scoring, state verification or release evidence."}

    @router.get("/team/credentials")
    def credentials(project_id: str, session=Depends(database)):
        get(session, Project, project_id)
        return {"items": [public(r) for r in session.scalars(select(CredentialVersion).filter_by(project_id=project_id).order_by(CredentialVersion.created_at.desc(), CredentialVersion.id))]}

    @router.post("/team/credentials", status_code=201)
    def create(body: NewCredential, session=Depends(database)):
        from agenteval.db import uid
        get(session, Project, body.project_id)
        header = "Authorization" if body.auth_mode == "bearer" else body.header_name
        if header.lower() in FORBIDDEN_HEADERS: raise HTTPException(422, detail={"code": "UNSAFE_AUTH_HEADER"})
        value = os.getenv(body.env_name, "")
        if not value or value != value.strip() or len(value) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise HTTPException(422, detail={"code": "CREDENTIAL_UNAVAILABLE"})
        version_id = uid()
        try: hashed = fingerprint(value, settings.credential_fingerprint_key, version_id)
        except CredentialError as exc: raise HTTPException(422, detail={"code": exc.code}) from None
        row = CredentialVersion(id=version_id, project_id=body.project_id, name=body.name, env_name=body.env_name,
            auth_mode=body.auth_mode, header_name=header, fingerprint=hashed)
        session.add(row); session.commit(); return public(row)

    @router.post("/team/credentials/{credential_version_id}/revoke")
    def revoke(credential_version_id: str, session=Depends(database)):
        row = get(session, CredentialVersion, credential_version_id); row.revoked = True; session.commit()
        return public(row)
