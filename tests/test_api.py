import json
from copy import deepcopy

from examples.demo import CAPABILITIES, EVALUATORS


def test_authentication(client):
    assert client.get("/health", headers={"Authorization": ""}).status_code == 200
    assert client.get("/api/v1/projects", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/v1/openapi.json").status_code == 200
    assert client.get("/openapi.json").status_code == 404


def test_oversized_requests_are_rejected_before_json_parsing(client):
    response = client.post("/api/v1/projects", content=b"x" * (2 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert response.json()["code"] == "REQUEST_TOO_LARGE"


def test_complete_regression_and_historical_regrading(client, setup_run):
    s, post = setup_run, setup_run["post"]
    baseline = post("evaluations", s["body"])
    baseline_report = client.get(f"/api/v1/evaluations/{baseline['id']}/report").json()
    assert baseline_report["run_status"] == "completed"
    assert baseline_report["success_rate"] == 1
    assert baseline_report["decision_coverage"] == 1
    assert baseline_report["scoring_coverage"] == 1
    version = post(f"agents/{s['agent']['id']}/versions", {
        "version": "2.0", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": CAPABILITIES})
    candidate = post("evaluations", {**s["body"], "agent_version_id": version["id"]})
    comparison = post("regressions/compare", {"baseline_batch_id": baseline["grading_batch_id"],
                      "candidate_batch_id": candidate["grading_batch_id"]})
    assert comparison["report"]["new_failures"] == ["order-1001"]
    assert comparison["report"]["success_rate_delta"] == -.5
    assert comparison["report"]["gate"]["status"] == "failed"
    bad = client.get(f"/api/v1/evaluations/{candidate['id']}/bad-cases").json()
    assert len(bad["items"]) == 1
    execution = client.get(f"/api/v1/executions/{bad['items'][0]['execution_id']}").json()
    trace = client.get(f"/api/v1/traces/{execution['trace_id']}").json()
    assert trace["observations"]["tool_calls"][0]["name"] == "get_order"
    regraded = post(f"evaluations/{candidate['id']}/regrade", None)
    assert regraded["revision"] == 1
    old = client.get(f"/api/v1/evaluations/{candidate['id']}/report", params={"grading_batch_id": candidate["grading_batch_id"]}).json()
    latest = client.get(f"/api/v1/evaluations/{candidate['id']}/report").json()
    assert old["grading_batch_id"] != latest["grading_batch_id"]
    assert old["counts"] == latest["counts"]
    frozen = client.get(f"/api/v1/regressions/{comparison['id']}").json()
    assert frozen["report"] == comparison["report"]
    assert client.get(f"/api/v1/evaluations/{candidate['id']}").json()["status"] == "completed"


def test_run_creation_idempotency_and_conflict(client, setup_run):
    s = setup_run
    headers = {"Idempotency-Key": "run-creation-key"}
    one = client.post("/api/v1/evaluations", json=s["body"], headers=headers)
    two = client.post("/api/v1/evaluations", json=s["body"], headers=headers)
    assert one.status_code == two.status_code == 202
    assert one.json()["id"] == two.json()["id"]
    version = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "2", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": CAPABILITIES})
    conflict = client.post("/api/v1/evaluations", json={**s["body"], "agent_version_id": version["id"]}, headers=headers)
    assert conflict.status_code == 409
    assert len(client.get("/api/v1/evaluations").json()["items"]) == 1


def test_capability_preflight_and_version_immutability(client, setup_run):
    s = setup_run
    blind = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "blind", "configuration": {"plugin_id": "example.order.v1"}, "capabilities": []})
    response = client.post("/api/v1/evaluations", json={**s["body"], "agent_version_id": blind["id"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PREFLIGHT_FAILED"
    assert client.get("/api/v1/evaluations").json()["items"] == []
    duplicate = client.post(f"/api/v1/agents/{s['agent']['id']}/versions", json={
        "version": "1.0", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": []})
    assert duplicate.status_code == 409
    assert client.patch(f"/api/v1/agents/{s['agent']['id']}/versions", json={}).status_code == 405


def test_benchmark_hash_stable_across_order_and_version_name(client, setup_run):
    s = setup_run
    changed = deepcopy(s["data"])
    changed["version"] = "same-content"
    changed["cases"].reverse()
    version = s["post"](f"benchmarks/{s['benchmark']['id']}/versions", changed)
    assert version["content_hash"] == s["benchmark_version"]["content_hash"]


def test_strict_comparison_rejects_profile_difference(client, setup_run):
    s, post = setup_run, setup_run["post"]
    one = post("evaluations", s["body"])
    profile = post("profile-versions", {"project_id": s["project"]["id"], "name": "Rules", "version": "2.0",
                   "evaluators": EVALUATORS, "execution": {"repetitions": 2}})
    two = post("evaluations", {**s["body"], "profile_version_id": profile["id"]})
    body = {"baseline_batch_id": one["grading_batch_id"], "candidate_batch_id": two["grading_batch_id"]}
    assert client.post("/api/v1/regressions/compare", json=body).status_code == 409
    exploratory = post("regressions/compare", {**body, "mode": "exploratory"})
    assert exploratory["report"]["gate"]["status"] == "inconclusive"
    assert exploratory["report"]["candidate"]["planned_trials"] == 4


def test_nonexistent_resource_and_pagination_bounds(client):
    assert client.get("/api/v1/evaluations/missing").status_code == 404
    assert client.get("/api/v1/projects?limit=10000").status_code == 422


def test_deny_arbitrary_plugin_and_http_credentials(client, setup_run):
    s = setup_run
    response = client.post(f"/api/v1/agents/{s['agent']['id']}/versions", json={
        "version": "unsafe", "configuration": {"plugin_id": "os.system"}})
    assert response.status_code == 422
    agent = s["post"]("agents", {"project_id": s["project"]["id"], "name": "HTTP", "adapter_type": "http"})
    for endpoint in ("http://user:password@localhost:9001/invoke", "http://169.254.169.254/metadata", "http://localhost:9001/invoke?token=secret"):
        response = client.post(f"/api/v1/agents/{agent['id']}/versions", json={"version": "bad", "configuration": {"endpoint": endpoint}})
        assert response.status_code == 422
        assert "password" not in response.text and "token=secret" not in response.text


def test_timeout_is_failure_and_does_not_inflate_repetition_denominator(client, setup_run):
    s, post = setup_run, setup_run["post"]
    slow = post(f"agents/{s['agent']['id']}/versions", {"version": "slow", "configuration": {"plugin_id": "example.slow"}, "capabilities": CAPABILITIES})
    profile = post("profile-versions", {"project_id": s["project"]["id"], "name": "Timeout", "version": "1",
                   "evaluators": EVALUATORS, "execution": {"timeout_seconds": .15}})
    run = post("evaluations", {**s["body"], "agent_version_id": slow["id"], "profile_version_id": profile["id"]})
    result = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert result["counts"] == {"passed": 0, "failed": 2, "unknown": 0, "excluded": 0}
    assert result["success_rate"] == 0 and result["decision_coverage"] == 1
    assert result["execution_counts"] == {"timed_out": 2}

