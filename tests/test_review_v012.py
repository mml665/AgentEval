from datetime import datetime, timedelta, timezone
import json
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from agenteval.db import GradingBatch, OnboardingReceipt, Run, TeamUser
from test_product_workflow import guided_body
from test_team_security import team, resources


def test_scope_options_are_bounded_with_large_history_and_exact_old_batch(client, setup_run):
    fixture = setup_run
    post = fixture["post"]
    project_id = fixture["project"]["id"]
    old = post("evaluations", fixture["body"])
    count = 10_050
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    with client.app.state.sessions() as session:
        session.execute(insert(Run.__table__), [
            {"id": f"review-run-{i:05d}", "project_id": project_id, **fixture["body"], "created_at": now + timedelta(seconds=i),
             "status": "completed", "manifest": {}, "manifest_hash": "scope-fixture"}
            for i in range(count)
        ])
        session.execute(insert(GradingBatch.__table__), [
            {"id": f"review-batch-{i:05d}", "run_id": f"review-run-{i:05d}", "revision": 0,
             "status": "completed", "grading_manifest": {}, "created_at": now}
            for i in range(count)
        ])
        session.commit()
    base = f"/api/v1/quality/scope-selection/{project_id}"
    started = perf_counter()
    response = client.get(base, params={"candidate_run_id": old["id"]})
    elapsed_ms = (perf_counter() - started) * 1000
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["selection"]["candidate_run_id"] == old["id"]
    assert data["selection"]["candidate_batch_id"] == old["grading_batch_id"]
    assert all(len(page["items"]) == 1 and page["next_offset"] is None for page in data["options"].values())
    assert len(response.content) < 4000
    print(json.dumps({"history_runs": count + 1, "history_batches": count + 1,
        "exact_scope_requests": 1, "response_bytes": len(response.content), "elapsed_ms": round(elapsed_ms, 1)}))
    endpoint = f"/api/v1/quality/scope-options/{project_id}"
    first = client.get(endpoint, params={"kind": "run", "limit": 2}).json()
    second = client.get(endpoint, params={"kind": "run", "limit": 2, "offset": first["next_offset"]}).json()
    assert first["next_offset"] == 2
    assert {item["id"] for item in first["items"]}.isdisjoint(item["id"] for item in second["items"])
    assert client.get(base, params={"candidate_run_id": old["id"], "candidate_batch_id": "review-batch-00000"}).status_code == 422
    run_only = client.get(endpoint, params={"kind": "batch", "run_id": old["id"]}).json()
    assert [item["id"] for item in run_only["items"]] == [old["grading_batch_id"]]


def test_pending_run_scope_does_not_borrow_evidence_from_matching_run(team):
    t = team
    source = resources(t, t["a"])
    project_id = t["a"]["id"]
    with t["app"].state.sessions() as session:
        run = Run(project_id=project_id, **source["body"], status="queued", manifest={}, manifest_hash="review-pending")
        session.add(run)
        session.flush()
        session.commit()
        run_id = run.id
    base = f"/api/v1/quality/scope-selection/{project_id}"
    assert t["outsider"]["client"].get(base, params={"candidate_run_id": run_id}).status_code == 404
    reader = t["reader"]["client"]
    result = reader.get(base, params={"candidate_run_id": run_id}).json()
    selection = result["selection"]
    assert selection["candidate_run_id"] == run_id and selection["candidate_batch_id"] == ""
    workbench = reader.get(f"/api/v1/quality/workbench/{project_id}", params=selection).json()
    assert workbench["evaluation_scope"]["candidate_run_id"] == run_id
    assert workbench["candidate"] is None and workbench["comparison"] is None
    direct = reader.get(f"/api/v1/quality/workbench/{project_id}", params={"candidate_run_id": run_id}).json()
    assert direct["candidate"] is None and direct["evaluation_scope"]["candidate_run_id"] == run_id


def test_first_project_receipt_recovers_after_rollback_and_is_visible_only_to_creator(team):
    t = team
    admin = t["admin"]
    body = {**guided_body(t["a"]["id"]), "project_id": None, "project_name": "First project recovery", "request_key": str(uuid4())}
    lookup = "/api/v1/onboarding/requests/" + body["request_key"]
    endpoint = "/api/v1/onboarding/evaluations"
    initial = admin.get("/api/v1/projects").json()["total"]

    def fail_receipt_commit(session):
        if any(isinstance(row, OnboardingReceipt) and row.request_key == body["request_key"] for row in session.new):
            raise HTTPException(503, detail={"code": "REVIEW_FIXTURE_TRANSACTION_ABORT"})

    event.listen(Session, "before_commit", fail_receipt_commit)
    try:
        assert admin.post(endpoint, json=body).status_code == 503
    finally:
        event.remove(Session, "before_commit", fail_receipt_commit)
    assert admin.get(lookup).json() == {"found": False, "run": None}
    assert admin.get("/api/v1/projects").json()["total"] == initial

    created = admin.post(endpoint, json=body)
    assert created.status_code == 202, created.text
    run = created.json()
    assert admin.post(endpoint, json=body).json()["id"] == run["id"]
    assert admin.get(lookup).json() == {"found": True, "run": {"id": run["id"], "project_id": run["project_id"]}}
    assert admin.get("/api/v1/projects").json()["total"] == initial + 1
    other = t["outsider"]["client"]
    assert other.get(lookup).status_code == 403
    with t["app"].state.sessions() as session:
        session.get(TeamUser, t["outsider"]["user"]["id"]).is_admin = True
        session.commit()
    assert other.get(lookup).json() == {"found": False, "run": None}
