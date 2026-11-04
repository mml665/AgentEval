from copy import deepcopy
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.db import Base, IssueEvent, MachineToken, Run, SecurityAudit, TeamUser, UserSession
from agenteval.security import bootstrap_admin

ORIGIN = "http://127.0.0.1"
PASSWORD = "test-password-for-fixture-only"


@pytest.fixture
def team(tmp_path):
    app = create_app(Settings(database_url="sqlite:///" + (tmp_path / "team.db").as_posix(), auth_mode="team",
        api_token="legacy-disabled-test-token", session_cookie_secure=False, session_origins=(ORIGIN,),
        environment_root=str(tmp_path / "environments")))
    Base.metadata.create_all(app.state.engine)
    bootstrap_admin(app.state.sessions, "admin", PASSWORD)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as admin:
        assert admin.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
        admin.headers["X-CSRF-Token"] = admin.get("/api/v1/auth/session").json()["csrf_token"]
        def post(path, body=None):
            r = admin.post("/api/v1/" + path, json=body)
            assert r.status_code in (200, 201, 202), r.text
            return r.json()
        a, b = post("projects", {"name": "A"}), post("projects", {"name": "B"})
        users = {}
        for name, role in (("developer", "evaluator"), ("reader", "viewer"), ("outsider", "evaluator")):
            user = post("team/users", {"username": name, "password": PASSWORD, "display_name": name})
            post("team/members/" + (b["id"] if name == "outsider" else a["id"]), {"user_id": user["id"], "role": role})
            client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
            assert client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD}).status_code == 200
            client.headers["X-CSRF-Token"] = client.get("/api/v1/auth/session").json()["csrf_token"]
            users[name] = {"client": client, "user": user}
        yield {"app": app, "admin": admin, "post": post, "a": a, "b": b, **users}
        for value in users.values(): value["client"].close()


def resources(t, project):
    p = t["post"]
    agent = p("agents", {"project_id": project["id"], "name": "Orders", "adapter_type": "python"})
    av = p(f"agents/{agent['id']}/versions", {"version": "bad", "configuration": {"plugin_id": "example.order.v2"}})
    bm = p("benchmarks", {"project_id": project["id"], "name": "Orders"})
    bv = p(f"benchmarks/{bm['id']}/versions", {"version": "1", "cases": [{"case_key": "one", "input": {"order_id": "1001"}, "assertions": {"expected_output": {"order_id": "1001", "status": "shipped"}}}]})
    profile = p("profile-versions", {"project_id": project["id"], "name": "Rules", "version": "1", "evaluators": [{"instance_id": "answer", "id": "exact_match"}]})
    body = {"agent_version_id": av["id"], "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]}
    run = p("evaluations", body)
    report = t["admin"].get(f"/api/v1/evaluations/{run['id']}/report").json()
    execution_id = report["trials"][0]["execution_id"]
    issue = t["admin"].get("/api/v1/quality/issues", params={"project_id": project["id"]}).json()["items"][0]
    compare = p("regressions/compare", {"baseline_batch_id": run["grading_batch_id"], "candidate_batch_id": run["grading_batch_id"]})
    decision = p("release-decisions", {"comparison_id": compare["id"]})
    return {"agent": agent, "av": av, "bm": bm, "bv": bv, "profile": profile, "body": body,
        "run": run, "execution_id": execution_id, "issue": issue, "comparison": compare, "decision": decision}


def test_personal_cookie_csrf_logout_and_disabled_legacy(team):
    t = team; c = t["developer"]["client"]
    assert c.get("/api/v1/auth/session").json()["id"] == t["developer"]["user"]["id"]
    token = c.headers.pop("X-CSRF-Token")
    assert c.post("/api/v1/agents", json={"project_id": t["a"]["id"], "name": "denied", "adapter_type": "python"}).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert c.post("/api/v1/agents", headers={"Origin": "https://evil.invalid"}, json={"project_id": t["a"]["id"], "name": "denied", "adapter_type": "python"}).status_code == 403
    assert c.get("/api/v1/projects", headers={"Authorization": "Bearer legacy-disabled-test-token"}).status_code == 401
    cookie = c.cookies.get("agenteval_session")
    with t["app"].state.sessions() as session:
        row = session.scalar(select(UserSession).where(UserSession.user_id == t["developer"]["user"]["id"]))
        assert row.token_digest != cookie and cookie not in row.token_digest
    assert c.post("/api/v1/auth/logout").json()["session_revoked"]
    assert c.get("/api/v1/projects").status_code == 401


def test_login_cookie_attributes_origin_rate_limit_and_uniform_errors(team):
    client = TestClient(team["app"], base_url=ORIGIN, headers={"Origin": ORIGIN})
    r = client.post("/api/v1/auth/login", json={"username": "reader", "password": PASSWORD})
    assert "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"]
    assert "token" not in r.json()
    assert client.post("/api/v1/auth/login", headers={"Origin": "https://evil.invalid"}, json={"username": "reader", "password": PASSWORD}).status_code == 403
    for _ in range(10):
        response = client.post("/api/v1/auth/login", json={"username": "not-a-user", "password": "wrong"})
        assert response.status_code == 401 and response.json()["detail"]["code"] == "LOGIN_REJECTED"
    assert client.post("/api/v1/auth/login", json={"username": "not-a-user", "password": "wrong"}).status_code == 429
    client.close()


def test_project_visibility_read_only_and_administrative_operations(team):
    from fastapi import Depends
    t = team
    for key in ("developer", "reader"):
        c = t[key]["client"]
        assert [p["id"] for p in c.get("/api/v1/projects").json()["items"]] == [t["a"]["id"]]
        assert c.get(f"/api/v1/workspace/{t['b']['id']}").status_code == 404
        assert c.get("/api/v1/evaluations").status_code == 403
        assert c.get("/api/v1/team/users").status_code == 403
        assert c.post("/api/v1/quality/policy/" + t["a"]["id"], json={"ingestion_mode": "manual"}).status_code == 403
    reader = t["reader"]["client"]
    assert reader.post("/api/v1/agents", json={"project_id": t["a"]["id"], "name": "forbidden", "adapter_type": "python"}).status_code == 403
    assert t["developer"]["client"].post("/api/v1/agents", json={"project_id": t["a"]["id"], "name": "allowed", "adapter_type": "python"}).status_code == 201
    @t["app"].get("/api/v1/unclassified-feature", dependencies=[Depends(t["app"].state.security.authorize)])
    def unclassified(): return {"value": "must not become visible merely by supplying a project ID"}
    assert reader.get("/api/v1/unclassified-feature", params={"project_id": t["a"]["id"]}).status_code == 403
    assert t["developer"]["client"].post("/api/v1/onboarding/check", json={"adapter_type": "python", "configuration": {"plugin_id": "example.order.v1"}}).status_code == 403


def test_direct_ids_trace_exports_and_cross_project_body_never_leak(team):
    t = team; a, b = resources(t, t["a"]), resources(t, t["b"])
    reader = t["reader"]["client"]
    execution = t["admin"].get(f"/api/v1/executions/{b['execution_id']}").json()
    paths = [f"evaluations/{b['run']['id']}", f"executions/{b['execution_id']}", f"executions/{b['execution_id']}/trace",
        f"executions/{b['execution_id']}/evidence-summary?grading_batch_id={b['run']['grading_batch_id']}", f"traces/{execution['trace_id']}",
        f"evaluations/{b['run']['id']}/export?grading_batch_id={b['run']['grading_batch_id']}",
        f"regressions/{b['comparison']['id']}", f"regressions/{b['comparison']['id']}/export", f"quality/issues/{b['issue']['id']}",
        f"release-decisions/{b['decision']['id']}", f"release-decisions/{b['decision']['id']}/export"]
    for path in paths: assert reader.get("/api/v1/" + path).status_code == 404, path
    assert reader.get(f"/api/v1/evaluations/{a['run']['id']}/export?grading_batch_id={a['run']['grading_batch_id']}").status_code == 200
    assert reader.get("/api/v1/regressions", params={"project_id": t["a"]["id"]}).json()["total"] == 1
    mixed = {**a["body"], "profile_version_id": b["profile"]["id"]}
    assert t["developer"]["client"].post("/api/v1/evaluations", json=mixed).status_code == 404
    assert reader.post(f"/api/v1/evaluations/{a['run']['id']}/regrade", json={}).status_code == 403


def test_machine_scope_expiry_revocation_and_no_digest_in_api(team):
    t = team
    issued = t["post"]("team/machines", {"name": "CI", "project_ids": [t["a"]["id"]], "permission": "read", "expires_in_seconds": 600})
    c = TestClient(t["app"], headers={"Authorization": "Bearer " + issued["token"]})
    assert c.get("/api/v1/projects").json()["total"] == 1
    assert c.get(f"/api/v1/workspace/{t['b']['id']}").status_code == 404
    assert c.post("/api/v1/agents", json={"project_id": t["a"]["id"], "name": "forbidden", "adapter_type": "python"}).status_code == 403
    assert issued["token"] not in t["admin"].get("/api/v1/team/machines").text
    assert "token_digest" not in t["admin"].get("/api/v1/team/machines").text
    with t["app"].state.sessions() as session:
        row = session.get(MachineToken, issued["id"]); row.expires_at = time.time() - 1; session.commit()
    assert c.get("/api/v1/projects").status_code == 401
    with t["app"].state.sessions() as session:
        session.get(MachineToken, issued["id"]).expires_at = time.time() + 600; session.commit()
    t["post"](f"team/machines/{issued['id']}/revoke")
    assert c.get("/api/v1/projects").status_code == 401
    c.close()


def test_session_expiry_membership_change_disable_and_password_reset_are_live(team):
    t = team; c = t["developer"]["client"]; user_id = t["developer"]["user"]["id"]
    t["post"]("team/members/" + t["a"]["id"], {"user_id": user_id, "role": "viewer"})
    assert c.post("/api/v1/agents", json={"project_id": t["a"]["id"], "name": "now read only", "adapter_type": "python"}).status_code == 403
    t["post"]("team/members/" + t["a"]["id"], {"user_id": user_id, "role": "remove"})
    assert c.get(f"/api/v1/workspace/{t['a']['id']}").status_code == 404
    t["post"]("team/users/" + user_id + "/password", {"password": "replacement-password-for-tests"})
    assert c.get("/api/v1/projects").status_code == 401
    reader = t["reader"]["client"]
    with t["app"].state.sessions() as session:
        row = session.scalar(select(UserSession).where(UserSession.user_id == t["reader"]["user"]["id"]))
        row.last_seen = time.time() - 4000; session.commit()
    assert reader.get("/api/v1/projects").status_code == 401
    with t["app"].state.sessions() as session:
        row = session.scalar(select(UserSession).where(UserSession.user_id == t["reader"]["user"]["id"]))
        row.last_seen, row.expires_at = time.time(), time.time() - 1
        session.commit()
    assert reader.get("/api/v1/projects").status_code == 401  # Absolute expiry even with fresh activity.
    outsider_id = t["outsider"]["client"].get("/api/v1/auth/session").json()["session_id"]
    assert c.post(f"/api/v1/auth/sessions/{outsider_id}/revoke").status_code == 401  # Password reset already revoked c.
    outsider = t["outsider"]["client"]
    t["post"]("team/users/" + t["outsider"]["user"]["id"] + "/state", {"active": False})
    assert outsider.get("/api/v1/projects").status_code == 401
    assert t["admin"].post("/api/v1/team/users/" + t["admin"].get("/api/v1/auth/session").json()["id"] + "/state", json={"active": False}).status_code == 409


def test_actual_initiator_member_assignment_and_audit_actor(team):
    t = team; a = resources(t, t["a"]); developer = t["developer"]["client"]
    made = developer.post("/api/v1/evaluations", json=a["body"])
    assert made.status_code == 202, made.text
    run = developer.get("/api/v1/evaluations/" + made.json()["id"]).json()
    assert run["quality_summary"]["initiator"]["id"] == t["developer"]["user"]["id"]
    issue = a["issue"]
    body = {"expected_revision": issue["revision"], "status": "confirmed", "note": "Confirmed", "assignee_user_id": t["developer"]["user"]["id"]}
    assigned = developer.post(f"/api/v1/quality/issues/{issue['id']}/update", json=body)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee_user_id"] == t["developer"]["user"]["id"]
    assert developer.post(f"/api/v1/quality/issues/{issue['id']}/update", json={**body, "expected_revision": 1, "assignee_user_id": t["outsider"]["user"]["id"]}).status_code == 422
    detail = developer.get(f"/api/v1/quality/issues/{issue['id']}").json()
    assert detail["events"][-1]["actor"]["id"] == t["developer"]["user"]["id"]
    with t["app"].state.sessions() as session:
        audit = session.scalar(select(SecurityAudit).where(SecurityAudit.action == "POST /quality/issues/{issue_id}/update", SecurityAudit.status == 200).order_by(SecurityAudit.created_at.desc()))
        assert audit.actor["id"] == t["developer"]["user"]["id"]
        assert audit.id == detail["events"][-1]["actor"]["audit_id"]
        membership = session.scalar(select(SecurityAudit).where(SecurityAudit.action == "POST /team/members/{project_id}", SecurityAudit.status == 200).order_by(SecurityAudit.created_at.desc()))
        assert membership.resource_ids["user_id"] == t["outsider"]["user"]["id"]
        assert membership.resource_ids["role"] == "evaluator"
    own = developer.get("/api/v1/auth/session").json()["session_id"]
    other = t["reader"]["client"].get("/api/v1/auth/session").json()["session_id"]
    assert developer.post(f"/api/v1/auth/sessions/{other}/revoke").status_code == 404
    assert developer.post(f"/api/v1/auth/sessions/{own}/revoke").status_code == 200
    assert developer.get("/api/v1/projects").status_code == 401


def test_cli_initialization_is_one_time_and_never_prints_password(tmp_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, "PYTHONPATH": "backend", "AGENTEVAL_DATABASE_URL": "sqlite:///" + (tmp_path / "bootstrap.db").as_posix(),
        "AGENTEVAL_AUTH_MODE": "team", "AGENTEVAL_BOOTSTRAP_PASSWORD": PASSWORD}
    def run(*args): return subprocess.run([sys.executable, "-m", "agenteval", *args], env=env, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert run("init-db").returncode == 0
    first = run("init-admin", "--username", "firstadmin")
    assert first.returncode == 0 and PASSWORD not in first.stdout + first.stderr
    second = run("init-admin", "--username", "anotheradmin")
    assert second.returncode != 0 and PASSWORD not in second.stdout + second.stderr
    env["AGENTEVAL_BOOTSTRAP_PASSWORD"] = "short-secret"
    invalid = run("init-admin", "--username", "newadmin")
    assert invalid.returncode != 0 and "short-secret" not in invalid.stdout + invalid.stderr
