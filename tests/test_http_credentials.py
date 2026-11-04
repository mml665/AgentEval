from dataclasses import replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.db import Base

SECRET = "fixture-http-key-not-a-real-secret"
TOKEN = "fixture-platform-token-not-real"


@pytest.fixture
def authenticated_http(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTEVAL_AGENT_SECRET_FIXTURE", SECRET)
    settings = Settings(database_url="sqlite:///" + (tmp_path / "http.db").as_posix(), api_token=TOKEN,
        credential_fingerprint_key="fixture-fingerprint-key-only-32-characters", http_allowlist=("agent.test:443",))
    app = create_app(settings); Base.metadata.create_all(app.state.engine)
    calls = []; state = {"mode": "ok"}
    original = httpx.AsyncClient
    def handler(request):
        calls.append((request.method, request.headers.get("Authorization"), request.headers.get("X-API-Key")))
        if request.method == "HEAD": return httpx.Response(405)
        if state["mode"] == "401": return httpx.Response(401)
        if state["mode"] == "403": return httpx.Response(403)
        if state["mode"] == "redirect": return httpx.Response(302, headers={"Location": "https://evil.invalid"})
        if state["mode"] == "invalid_json": return httpx.Response(200, content=b"not-json")
        if state["mode"] == "protocol": return httpx.Response(200, json={"answer": "missing output"})
        if state["mode"] == "timeout": raise httpx.ReadTimeout("fixture timeout")
        if state["mode"] == "reflection": return httpx.Response(200, json={"output": SECRET})
        if state["mode"] == "encoded_reflection":
            from urllib.parse import quote
            return httpx.Response(200, json={"output": "https://agent.test/?key=" + quote(SECRET, safe="").replace("-", "%2d")})
        if state["mode"] == "business": return httpx.Response(200, json={"output": "failed", "status": "agent_failed"})
        return httpx.Response(200, json={"output": "ok"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a,**kw: original(*a,transport=httpx.MockTransport(handler),**kw))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        def post(path, body=None, **kwargs):
            r = client.post("/api/v1/" + path, json=body, **kwargs)
            assert r.status_code in (200, 201, 202), r.text
            return r.json()
        project = post("projects", {"name": "HTTP"})
        cred = post("team/credentials", {"project_id": project["id"], "name": "Fixture bearer v1", "env_name": "AGENTEVAL_AGENT_SECRET_FIXTURE", "auth_mode": "bearer"})
        agent = post("agents", {"project_id": project["id"], "name": "Authenticated service", "adapter_type": "http"})
        av = post(f"agents/{agent['id']}/versions", {"version": "1", "configuration": {"endpoint": "https://agent.test/invoke", "credential_version_id": cred["id"]}})
        bm = post("benchmarks", {"project_id": project["id"], "name": "Explicit fixture"})
        bv = post(f"benchmarks/{bm['id']}/versions", {"version": "1", "cases": [{"case_key": "one", "input": {"query": "test"}, "assertions": {"expected_output": "ok"}}]})
        case = client.get(f"/api/v1/benchmark-versions/{bv['id']}/cases").json()["items"][0]
        profile = post("profile-versions", {"project_id": project["id"], "name": "Exact", "version": "1", "evaluators": [{"instance_id": "answer", "id": "exact_match"}]})
        body = {"agent_version_id": av["id"], "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]}
        yield {"client": client, "post": post, "settings": settings, "project": project, "cred": cred,
            "agent": agent, "av": av, "bv": bv, "case": case, "body": body, "calls": calls, "state": state}


def test_bearer_runs_freeze_reference_and_never_store_plain_key(authenticated_http):
    t = authenticated_http
    assert not t["calls"]  # Registration and editing never invokes the business service.
    run = t["post"]("evaluations", t["body"])
    assert t["calls"] == [("POST", "Bearer " + SECRET, None)]
    report = t["client"].get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["success_rate"] == 1
    assert run["manifest"]["agent"]["configuration"]["credential_version_id"] == t["cred"]["id"]
    for path in (f"evaluations/{run['id']}", f"evaluations/{run['id']}/export?grading_batch_id={run['grading_batch_id']}", f"executions/{report['trials'][0]['execution_id']}/trace", f"team/credentials?project_id={t['project']['id']}"):
        response = t["client"].get("/api/v1/" + path)
        assert response.status_code == 200 and SECRET not in response.text and "fingerprint" not in response.text


def test_head_405_is_not_protocol_failure_and_test_call_requires_explicit_case(authenticated_http):
    t = authenticated_http
    check = t["post"]("onboarding/check", {"project_id": t["project"]["id"], "adapter_type": "http", "configuration": t["av"]["configuration"]})
    assert check["http_status"] == 405 and check["checks"]["protocol"] == "not_verified" and not check["capabilities_verified"]
    assert all(c[0] == "HEAD" for c in t["calls"])
    body = {"agent_version_id": t["av"]["id"], "benchmark_version_id": t["bv"]["id"], "case_id": t["case"]["id"], "confirm_test_environment": False}
    assert t["client"].post("/api/v1/onboarding/test-call", json=body).status_code == 422
    result = t["post"]("onboarding/test-call", {**body, "confirm_test_environment": True})
    assert result["status"] == "protocol_verified" and result["stored_as_evaluation"] is False
    assert t["client"].get("/api/v1/evaluations", params={"project_id": t["project"]["id"]}).json()["total"] == 0


@pytest.mark.parametrize("mode,code", [("401", "HTTP_AUTH_REJECTED"), ("403", "HTTP_PERMISSION_DENIED"),
    ("redirect", "HTTP_REDIRECT_REJECTED"), ("invalid_json", "HTTP_INVALID_JSON"), ("protocol", "HTTP_PROTOCOL_ERROR"),
    ("business", "HTTP_BUSINESS_FAILURE"), ("reflection", "HTTP_SENSITIVE_RESPONSE_REJECTED"), ("encoded_reflection", "HTTP_SENSITIVE_RESPONSE_REJECTED")])
def test_http_failure_codes_and_credential_echo_rejection(authenticated_http, mode, code):
    t = authenticated_http; t["state"]["mode"] = mode
    run = t["post"]("evaluations", t["body"])
    r = t["client"].get(f"/api/v1/evaluations/{run['id']}/report").json()
    execution = t["client"].get(f"/api/v1/executions/{r['trials'][0]['execution_id']}").json()
    assert execution["error"]["code"] == code
    exported = t["client"].get(f"/api/v1/evaluations/{run['id']}/export?grading_batch_id={run['grading_batch_id']}")
    assert SECRET not in exported.text
    assert len(t["calls"]) == 1


def test_credential_revocation_rotation_idempotent_replay_and_regrade(authenticated_http, monkeypatch):
    t = authenticated_http
    run = t["post"]("evaluations", t["body"], headers={"Idempotency-Key": "credential-run-once"})
    monkeypatch.setenv("AGENTEVAL_AGENT_SECRET_FIXTURE", "replacement-fixture-secret-not-real")
    changed = t["client"].post("/api/v1/evaluations", json=t["body"])
    assert changed.status_code == 422 and changed.json()["detail"]["code"] == "CREDENTIAL_VERSION_CHANGED"
    t["post"](f"team/credentials/{t['cred']['id']}/revoke")
    assert t["client"].post("/api/v1/evaluations", json=t["body"]).json()["detail"]["code"] == "CREDENTIAL_REVOKED"
    replay = t["post"]("evaluations", t["body"], headers={"Idempotency-Key": "credential-run-once"})
    assert replay["id"] == run["id"]
    t["post"](f"evaluations/{run['id']}/regrade", {})
    assert len(t["calls"]) == 1


def test_named_header_no_auth_and_credential_scope_validation(authenticated_http):
    t = authenticated_http
    ref = t["post"]("team/credentials", {"project_id": t["project"]["id"], "name": "Header", "env_name": "AGENTEVAL_AGENT_SECRET_FIXTURE", "auth_mode": "header", "header_name": "X-API-Key"})
    av = t["post"](f"agents/{t['agent']['id']}/versions", {"version": "2", "configuration": {"endpoint": "https://agent.test/invoke", "credential_version_id": ref["id"]}})
    t["post"]("evaluations", {**t["body"], "agent_version_id": av["id"]})
    assert t["calls"][-1] == ("POST", None, SECRET)
    av3 = t["post"](f"agents/{t['agent']['id']}/versions", {"version": "3", "configuration": {"endpoint": "https://agent.test/invoke"}})
    t["post"]("evaluations", {**t["body"], "agent_version_id": av3["id"]})
    assert t["calls"][-1] == ("POST", None, None)
    other = t["post"]("projects", {"name": "Other"})
    agent = t["post"]("agents", {"project_id": other["id"], "name": "Other HTTP", "adapter_type": "http"})
    assert t["client"].post(f"/api/v1/agents/{agent['id']}/versions", json={"version": "1", "configuration": t["av"]["configuration"]}).status_code == 422
    assert t["client"].post("/api/v1/team/credentials", json={"project_id": t["project"]["id"], "name": "forbidden", "env_name": "AGENTEVAL_API_TOKEN", "auth_mode": "bearer"}).status_code == 422


def test_http_test_timeout_is_unknown(authenticated_http):
    t = authenticated_http; t["state"]["mode"] = "timeout"
    response = t["post"]("onboarding/test-call", {"agent_version_id": t["av"]["id"], "benchmark_version_id": t["bv"]["id"], "case_id": t["case"]["id"], "confirm_test_environment": True})
    assert response["status"] == "unknown" and response["remote_outcome"] == "unknown"


async def test_plugin_subprocess_environment_excludes_platform_secrets(monkeypatch):
    from agenteval import adapters
    monkeypatch.setenv("AGENTEVAL_API_TOKEN", "must-not-reach-plugin")
    monkeypatch.setenv("AGENTEVAL_JUDGE_API_KEY", "must-not-reach-plugin")
    monkeypatch.setenv("AGENTEVAL_DATABASE_URL", "must-not-reach-plugin")
    monkeypatch.setenv("DEPLOYMENT_PLUGIN_FIXTURE", "explicitly-allowed")
    captured = {}; original = adapters.asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs): captured.update(kwargs["env"]); return await original(*args, **kwargs)
    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", spawn)
    value = await adapters.invoke("python", {"plugin_id": "example.order.v1"}, {"input": {"order_id": "1001"}}, 15,
        Settings(plugin_env_allowlist=("DEPLOYMENT_PLUGIN_FIXTURE", "AGENTEVAL_API_TOKEN")))
    assert value["output"]["status"] == "shipped"
    assert "AGENTEVAL_API_TOKEN" not in captured and "AGENTEVAL_DATABASE_URL" not in captured and "AGENTEVAL_JUDGE_API_KEY" not in captured
    assert captured["DEPLOYMENT_PLUGIN_FIXTURE"] == "explicitly-allowed"
