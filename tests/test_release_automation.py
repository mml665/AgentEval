from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from scripts.ci_gate import run_gate


def test_ci_gate_uses_fixed_reusable_run_and_fails_closed(client, setup_run):
    s = setup_run
    baseline = s["post"]("evaluations", s["body"])
    passing = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "passing",
        "configuration": s["version"]["configuration"], "capabilities": s["version"]["capabilities"]})

    class Client:
        def get(self, path, **params):
            return client.get(f"/api/v1/{path}", params=params).raise_for_status().json()

        def post(self, path, data):
            return client.post(f"/api/v1/{path}", json=data).raise_for_status().json()

    payload = {**s["body"], "project_id": s["project"]["id"],
               "agent_version_id": passing["id"], "request_key": str(uuid4())}
    original = run_gate(Client(), payload, baseline["grading_batch_id"], 10)
    assert original["status"] == "ready", original
    assert run_gate(Client(), payload, baseline["grading_batch_id"], 10) == original
    assert Client().get("evaluations", project_id=payload["project_id"])["total"] == 2

    bad = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "bad",
        "configuration": {"plugin_id": "example.order.v2"}, "capabilities": s["version"]["capabilities"]})
    blocked = run_gate(Client(), {**payload, "agent_version_id": bad["id"], "request_key": str(uuid4())},
                       baseline["grading_batch_id"], 10)
    assert blocked["status"] == "blocked", blocked
    s["post"](f"evaluations/{baseline['id']}/regrade", {})
    insufficient = run_gate(Client(), payload, baseline["grading_batch_id"], 10)
    assert insufficient["status"] == "insufficient"
    assert "NEWER_GRADING_BATCH_EXISTS" in insufficient["reasons"]


def test_concurrent_ci_submissions_reuse_one_run(client, setup_run):
    payload = {**setup_run["body"], "project_id": setup_run["project"]["id"], "request_key": str(uuid4())}

    def submit(_):
        return client.post("/api/v1/onboarding/reuse", json=payload).raise_for_status().json()["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(submit, range(2)))
    assert ids[0] == ids[1]


def test_notifications_and_trends_keep_project_scope_and_trial_denominators(client, setup_run):
    s = setup_run
    project = s["project"]["id"]
    first, second = s["data"]["cases"]
    bv = s["post"](f"benchmarks/{s['benchmark']['id']}/versions", {"version": "tags",
        "cases": [{**first, "tags": ["orders", "shipped"]}, {**second, "tags": ["orders", "pending"]}]})
    bad = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "bad",
        "configuration": {"plugin_id": "example.order.v2"}, "capabilities": s["version"]["capabilities"]})
    scope = {**s["body"], "benchmark_version_id": bv["id"]}
    run = s["post"]("evaluations", scope)
    s["post"](f"evaluations/{run['id']}/regrade", {})
    bad_run = s["post"]("evaluations", {**scope, "agent_version_id": bad["id"]})

    feed = client.get(f"/api/v1/quality/notifications/{project}", params={"limit": 2}).json()
    assert feed["total"] >= 4
    full = client.get(f"/api/v1/quality/notifications/{project}").json()["items"]
    assert sum(row["kind"] == "task" for row in full) == 3
    assert any(row["kind"] == "issue" and row["action"] == "discovered" for row in full)
    assert any(row.get("grading_batch_id") == bad_run["grading_batch_id"] for row in full)
    assert all("detail" not in row and row["created_at"].endswith("+00:00") for row in full)
    other = s["post"]("projects", {"name": "Other"})["id"]
    assert client.get(f"/api/v1/quality/notifications/{other}").json()["total"] == 0
    assert client.get(f"/api/v1/quality/trends/{other}", params=scope).status_code == 422

    value = client.get(f"/api/v1/quality/trends/{project}", params={**scope, "days": 7}).json()
    assert len(value["series"]) == 7
    assert sum(day["planned_trials"] for day in value["series"]) == 2
    assert sum(day["passed"] for day in value["series"]) == 2
    assert value["series"][0]["success_rate"] is None
    assert value["tags_total"] == 3
    assert next(row for row in value["tags"] if row["label"] == "orders") == {
        "label": "orders", "cases": 2, "decided_cases": 2, "coverage": 1}
