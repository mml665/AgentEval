"""Session endpoints and administrator/member management; raw secrets never serialize."""
import asyncio
import time
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, func

from agenteval.db import (LoginThrottle, MachineToken, Project, ProjectMembership, SecurityAudit,
    TeamUser, UserSession)
from agenteval.security import digest, hash_password, lock_security, verify_password
from agenteval.team_contracts import Login, Membership, NewMachine, NewUser, PasswordChange, UserState


def public_user(user):
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "active": user.active, "is_admin": user.is_admin}


def install(app, router, security, settings, sessions, database, get, serialize):
    auth = APIRouter(prefix="/api/v1/auth")

    @auth.get("/options")
    def options(session=Depends(database)):
        return {"mode": settings.auth_mode, "legacy_enabled": security.legacy_enabled,
            "initialized": bool(session.scalar(select(TeamUser.id).where(TeamUser.is_admin.is_(True), TeamUser.active.is_(True)).limit(1))), "session_cookie_secure": settings.session_cookie_secure}

    @auth.post("/login")
    def login(body: Login, request: Request, response: Response):
        security.origin(request)
        now = time.time()
        with sessions() as session:
            lock_security(session)
            keys = ["ip:" + (request.client.host if request.client else "unknown"), "user:" + body.username]
            buckets = []
            for key in keys:
                key = digest(key)
                value = session.scalar(select(LoginThrottle).where(LoginThrottle.key == key))
                if not value:
                    value = LoginThrottle(key=key, attempts=0, window_end=now + 300); session.add(value)
                if value.window_end <= now: value.attempts, value.window_end = 0, now + 300
                buckets.append(value)
            if buckets[0].attempts >= 60 or buckets[1].attempts >= 10:
                raise HTTPException(429, detail={"code": "LOGIN_RATE_LIMITED"}, headers={"Retry-After": "300"})
            for value in buckets: value.attempts += 1
            user = session.scalar(select(TeamUser).where(TeamUser.username == body.username))
            # Equal work for unknown usernames. The dummy hash is never an account password.
            dummy = "scrypt-14-8-5$" + "00" * 16 + "$" + "00" * 64
            valid = verify_password(body.password, user.password_hash if user else dummy)
            if not user or not user.active or not valid:
                session.add(SecurityAudit(actor={"kind": "anonymous", "id": None, "display_name": "Unknown"}, action="LOGIN_REJECTED",
                    project_ids=[], resource_ids={}, status=401))
                session.commit()
                raise HTTPException(401, detail={"code": "LOGIN_REJECTED"})
            old = request.cookies.get(security.cookie_name)
            previous = session.scalar(select(UserSession).where(UserSession.token_digest == digest(old))) if old else None
            if previous: previous.revoked = True
            raw = secrets.token_urlsafe(32)
            value = UserSession(user_id=user.id, token_digest=digest(raw), csrf_token=secrets.token_urlsafe(32),
                expires_at=now + settings.session_absolute_seconds, last_seen=now)
            session.add(value); session.flush()
            session.add(SecurityAudit(actor={"kind": "user", "id": user.id, "display_name": user.display_name}, action="LOGIN",
                project_ids=[], resource_ids={"session_id": value.id}, status=200))
            session.commit()
        response.set_cookie(security.cookie_name, raw, httponly=True, secure=settings.session_cookie_secure, samesite="strict",
            path="/", max_age=settings.session_absolute_seconds)
        response.headers["Cache-Control"] = "no-store"
        return {"authenticated": True}

    @auth.get("/session")
    def current(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return security.authenticate(request).view()

    @auth.post("/logout")
    def logout(request: Request, response: Response):
        principal = security.authenticate(request)
        if principal.kind == "user":
            security.origin(request)
            if not secrets.compare_digest(request.headers.get("x-csrf-token", "").encode(), principal.csrf_token.encode()):
                raise HTTPException(403, detail={"code": "CSRF_REJECTED"})
            with sessions() as session:
                session.get(UserSession, principal.session_id).revoked = True
                session.add(SecurityAudit(actor=principal.actor(), action="LOGOUT", project_ids=[], resource_ids={"session_id": principal.session_id}, status=200))
                session.commit()
        response.delete_cookie(security.cookie_name, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return {"session_revoked": principal.kind == "user", "legacy_token_revoked": False}

    app.include_router(auth)

    @router.get("/auth/sessions")
    def session_list(request: Request, session=Depends(database)):
        principal = request.state.principal
        if principal.kind != "user": raise HTTPException(403, detail={"code": "PERSONAL_SESSION_REQUIRED"})
        return {"items": [{"id": s.id, "expires_at": s.expires_at, "last_seen": s.last_seen, "revoked": s.revoked}
            for s in session.scalars(select(UserSession).where(UserSession.user_id == principal.id).order_by(UserSession.created_at.desc()).limit(100))]}

    @router.post("/auth/sessions/{session_id}/revoke")
    def revoke_session(session_id: str, request: Request, session=Depends(database)):
        principal = request.state.principal; row = get(session, UserSession, session_id)
        if not principal.admin and (principal.kind != "user" or principal.id != row.user_id): raise HTTPException(404)
        row.revoked = True; session.commit(); return {"revoked": True}

    @router.get("/team/users")
    def users(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return {"items": [public_user(u) for u in session.scalars(select(TeamUser).order_by(TeamUser.username, TeamUser.id).offset(offset).limit(limit))],
            "total": session.scalar(select(func.count()).select_from(TeamUser))}

    @router.post("/team/users", status_code=201)
    def create_user(body: NewUser, session=Depends(database)):
        if not session.scalar(select(TeamUser.id).where(TeamUser.is_admin.is_(True), TeamUser.active.is_(True)).limit(1)):
            raise HTTPException(409, detail={"code": "TEAM_INITIALIZATION_REQUIRED"})
        user = TeamUser(username=body.username, display_name=body.display_name, is_admin=body.is_admin, password_hash=hash_password(body.password))
        session.add(user); session.commit(); return public_user(user)

    @router.post("/team/users/{user_id}/state")
    def user_state(user_id: str, body: UserState):
        with sessions() as session:
            lock_security(session); user = get(session, TeamUser, user_id)
            if not body.active and user.is_admin and user.active and session.scalar(select(func.count()).select_from(TeamUser).where(TeamUser.is_admin.is_(True), TeamUser.active.is_(True))) <= 1:
                raise HTTPException(409, detail={"code": "LAST_ADMIN_REQUIRED"})
            user.active = body.active
            if not body.active:
                for row in session.scalars(select(UserSession).where(UserSession.user_id == user.id)): row.revoked = True
            session.commit(); return public_user(user)

    @router.post("/team/users/{user_id}/password")
    def change_password(user_id: str, body: PasswordChange):
        with sessions() as session:
            lock_security(session); user = get(session, TeamUser, user_id); user.password_hash = hash_password(body.password)
            for row in session.scalars(select(UserSession).where(UserSession.user_id == user.id)): row.revoked = True
            session.commit()
        return {"password_changed": True, "sessions_revoked": True}

    @router.get("/team/members")
    def members(project_id: str, session=Depends(database)):
        get(session, Project, project_id)
        rows = session.execute(select(TeamUser, ProjectMembership).join(ProjectMembership, ProjectMembership.user_id == TeamUser.id).where(ProjectMembership.project_id == project_id).order_by(TeamUser.display_name, TeamUser.id))
        return {"items": [{"id": user.id, "display_name": user.display_name, "active": user.active, "role": membership.role} for user, membership in rows]}

    @router.post("/team/members/{project_id}")
    def set_member(project_id: str, body: Membership):
        with sessions() as session:
            lock_security(session); get(session, Project, project_id); get(session, TeamUser, body.user_id)
            value = session.scalar(select(ProjectMembership).filter_by(project_id=project_id, user_id=body.user_id))
            if body.role == "remove":
                if value: session.delete(value)
            elif value: value.role = body.role
            else: session.add(ProjectMembership(project_id=project_id, user_id=body.user_id, role=body.role))
            session.commit()
        return {"user_id": body.user_id, "project_id": project_id, "role": body.role}

    @router.get("/team/machines")
    def machines(session=Depends(database)):
        return {"items": [{k: v for k, v in serialize(row).items() if k != "token_digest"} for row in session.scalars(select(MachineToken).order_by(MachineToken.created_at.desc()).limit(100))]}

    @router.post("/team/machines", status_code=201)
    def new_machine(body: NewMachine, session=Depends(database)):
        for key in body.project_ids: get(session, Project, key)
        raw = "ae_" + secrets.token_urlsafe(32)
        row = MachineToken(name=body.name, token_digest=digest(raw), project_ids=sorted(set(body.project_ids)),
            permission=body.permission, expires_at=time.time() + body.expires_in_seconds)
        session.add(row); session.commit()
        return {"id": row.id, "token": raw, "expires_at": row.expires_at, "shown_once": True}

    @router.post("/team/machines/{machine_id}/revoke")
    def revoke_machine(machine_id: str, session=Depends(database)):
        get(session, MachineToken, machine_id).revoked = True; session.commit(); return {"revoked": True}

    @router.get("/team/audit")
    def audit(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return {"items": [serialize(r) for r in session.scalars(select(SecurityAudit).order_by(SecurityAudit.created_at.desc(), SecurityAudit.id).offset(offset).limit(limit))],
            "total": session.scalar(select(func.count()).select_from(SecurityAudit))}
