"""Derived display views only. Persisted evidence and hashes are never rewritten."""
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
import html
import json
import os
import re
from urllib.parse import quote, quote_plus

from sqlalchemy import select
from agenteval.contracts import digest
from agenteval.db import CredentialVersion, ProtectionPolicy

MARKER = "[REDACTED]"
FIELDS = {"api_key", "apikey", "password", "authorization", "access_token", "secret", "token",
    "client_secret", "private_key", "cookie", "set-cookie", "refresh_token"}
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
ASSIGNMENT = re.compile(r'''(?i)(\b(?:password|api[_-]?key|access[_-]?token|client[_-]?secret|secret)\s*[:=]\s*["']?)([^\s"'&,;<>]+)''')
URL_AUTH = re.compile(r"(https?://)[^/@\s]+:[^/@\s]+@", re.I)


@dataclass(frozen=True)
class ViewPolicy:
    versions: tuple = ()
    paths: tuple = ()
    literals: tuple = ()
    emails: bool = True
    available: bool = True

    def metadata(self):
        return {"kind": "redacted", "engine": "display-v1", "policy_versions": list(self.versions),
            "policy_hash": digest({"engine": "display-v1", "versions": self.versions, "paths": self.paths, "emails": self.emails}),
            "integrity_scope": "Derived view only; original hashes refer to protected stored evidence."}


view_context = ContextVar("evidence_view", default=None)


def load_policy(session, settings, projects):
    versions, paths, names, emails, available = [], [], [], False, True
    for project in sorted(projects):
        row = session.scalar(select(ProtectionPolicy).where(ProtectionPolicy.project_id == project).order_by(ProtectionPolicy.revision.desc()).limit(1))
        if row:
            versions.append(row.id); paths += row.definition["paths"]; names += row.definition["secret_env_names"]
            emails |= row.definition["redact_emails"]
            from agenteval.credentials import fingerprint, CredentialError
            for name in row.definition["secret_env_names"]:
                try:
                    available &= bool(os.getenv(name)) and fingerprint(os.getenv(name), settings.credential_fingerprint_key, row.id) == row.definition.get("secret_fingerprints", {}).get(name)
                except CredentialError: available = False
        else: emails = True
    names += list(session.scalars(select(CredentialVersion.env_name).where(CredentialVersion.project_id.in_(projects))))
    # Deployment secrets are included for accidental reflection, without exposing names/values to clients.
    values = [settings.api_token, settings.admin_token, settings.judge_api_key, settings.credential_fingerprint_key,
        settings.database_url, settings.broker_url, os.getenv("AGENTEVAL_ARCHIVE_SIGNING_KEY", "")]
    from urllib.parse import urlsplit, unquote
    values += [unquote(p.password) for p in (urlsplit(settings.database_url), urlsplit(settings.broker_url)) if p.password]
    values += [os.getenv(name, "") for name in names]
    variants = set()
    for value in values:
        if not value: continue
        variants.update((value, html.escape(value), json.dumps(value, ensure_ascii=True)[1:-1]))
        for _ in range(3):
            variants.update((quote(value, safe=""), quote_plus(value))); value = quote(value, safe="")
    return ViewPolicy(tuple(versions), tuple(sorted(set(paths))), tuple(sorted(variants, key=len, reverse=True)), emails or not projects, available)


@lru_cache(maxsize=4096)
def matches(pattern, path):
    parts = tuple(p.replace("~1", "/").replace("~0", "~") for p in pattern.split("/")[1:])
    @lru_cache(None)
    def walk(i, j):
        if i == len(parts): return j == len(path)
        if parts[i] == "**": return walk(i+1, j) or (j < len(path) and walk(i, j+1))
        return j < len(path) and (parts[i] == "*" or parts[i] == path[j]) and walk(i+1, j+1)
    return walk(0, 0)


def redact(value, policy=None, path=(), depth=0):
    policy = policy or view_context.get() or ViewPolicy()
    if not policy.available:
        from fastapi import HTTPException
        raise HTTPException(503, detail={"code": "REDACTION_POLICY_UNAVAILABLE"})
    if depth > 100: return MARKER
    if any(matches(p, path) for p in policy.paths): return MARKER
    if hasattr(value, "isoformat"): return value.isoformat()
    if isinstance(value, dict):
        # JSON property names can themselves contain PII or reflected secrets.
        return {text(str(k), policy): MARKER if str(k).lower() in FIELDS else redact(v, policy, path+(str(k),), depth+1) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [redact(v, policy, path+(str(i),), depth+1) for i,v in enumerate(value)]
    if isinstance(value, str):
        # OTel attributes often contain serialized JSON, rather than nested objects.
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
                return json.dumps(redact(parsed, policy, path, depth+1), ensure_ascii=False)
            except (ValueError, RecursionError): pass
        return text(value, policy)
    return value


def text(value, policy):
    for literal in policy.literals: value = value.replace(literal, MARKER)
    value = URL_AUTH.sub(r"\1[REDACTED]@", value)
    value = BEARER.sub("Bearer " + MARKER, value)
    value = ASSIGNMENT.sub(lambda m: m[1] + MARKER, value)
    if policy.emails: value = EMAIL.sub(MARKER, value)
    return value


class DisplayProtection:
    """Buffer bounded API responses; raw data only leaves an explicitly authorized route."""
    def __init__(self, app, security): self.app, self.security = app, security

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/v1/"):
            return await self.app(scope, receive, send)
        token = view_context.set(None)
        start, chunks, size, overflow = None, [], 0, False
        completed = False
        async def guarded(message):
            nonlocal start, size, overflow, completed
            if message["type"] == "http.response.start":
                start = message; return
            if message["type"] != "http.response.body": return await send(message)
            size += len(message.get("body", b""))
            if size > 16 * 1024 * 1024: overflow = True; chunks.clear()
            if not overflow: chunks.append(message.get("body", b""))
            if message.get("more_body", False): return
            data = b"".join(chunks)
            headers = list(start.get("headers", []))
            policy = scope.get("state", {}).get("protection_policy")
            raw = scope.get("state", {}).get("raw_evidence_authorized", False)
            route = getattr(scope.get("route"), "path", "")
            control = scope["path"].startswith("/api/v1/auth/") or (
                scope["path"].startswith("/api/v1/team/") and not scope["path"].startswith("/api/v1/team/audit")) or route in {
                    "/api/v1/protection/policy/{project_id}", "/api/v1/protection/grants/{project_id}", "/api/v1/protection/storage/{project_id}"}
            media = next((v for k,v in headers if k.lower() == b"content-type"), b"")
            if overflow:
                data = b'{"code":"DISPLAY_RESPONSE_TOO_LARGE","message":"Use paginated evidence endpoints."}'
                start["status"] = 413
            elif policy and not policy.available and not raw and not control:
                data = b'{"code":"REDACTION_POLICY_UNAVAILABLE"}'; start["status"] = 503
            elif policy and not raw and not control and b"application/json" in media and not scope["path"].endswith("/export"):
                try: data = json.dumps(redact(json.loads(data), policy), ensure_ascii=False, allow_nan=False).encode()
                except (ValueError, RecursionError):
                    data = b'{"code":"DISPLAY_PROTECTION_FAILED"}'; start["status"] = 500
            if policy and not control and not raw:
                import hashlib
                headers += [(b"x-agenteval-view", b"redacted"), (b"x-agenteval-policy", policy.metadata()["policy_hash"].encode()),
                    (b"x-agenteval-view-hash", hashlib.sha256(data).hexdigest().encode())]
            if raw: headers += [(b"x-agenteval-view", b"raw-audited")]
            headers = [(k,v) for k,v in headers if k.lower() not in (b"content-length", b"etag")]
            headers += [(b"content-length", str(len(data)).encode())]
            if start["status"] >= 400 and scope.get("state", {}).get("audit_id"):
                import asyncio
                await asyncio.to_thread(self.security.finish_audit, scope["state"]["audit_id"], start["status"])
            completed = True  # No second response if the client disconnects while sending.
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": data})
        try: await self.app(scope, receive, guarded)
        except Exception as exc:
            # Do not let framework traceback logging print request/SQL/Agent payloads.
            import logging
            logging.getLogger("agenteval").error("REQUEST_FAILED type=%s route=%s", type(exc).__name__, getattr(scope.get("route"), "path", "unmatched"))
            audit_id = scope.get("state", {}).get("audit_id")
            if audit_id:
                import asyncio
                try: await asyncio.to_thread(self.security.finish_audit, audit_id, 500)
                except Exception: pass
            if not completed:
                from starlette.responses import JSONResponse
                await JSONResponse({"code": "REQUEST_FAILED"}, status_code=500, headers={"Cache-Control": "no-store"})(scope, receive, send)
        finally: view_context.reset(token)
