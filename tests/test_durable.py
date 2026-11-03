import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.db import Base, Execution, GradingBatch, Run, Trial, WorkItem
from agenteval.durable import Coordinator
from tests.conftest import TOKEN


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'queue.db').as_posix()}", api_token=TOKEN,
                        execution_mode="celery", project_concurrency=1, admin_token="test-admin-token")
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    app.state.coordinator = Coordinator(app.state.sessions, settings)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


def ids(client, run_id):
    return [x["id"] for x in client.get(f"/api/v1/evaluations/{run_id}/work-items").json()["items"] if x["kind"] == "execute"]


def expire(coordinator, item_id):
    with coordinator.locked(coordinator.project(item_id)) as session:
        session.get(WorkItem, item_id).lease_until = time.time() - 1


def test_atomic_outbox_and_broker_outage_do_not_lose_run(client, setup_run):
    run = setup_run["post"]("evaluations", setup_run["body"])
    coord = client.app.state.coordinator
    assert len(ids(client, run["id"])) == 2
    class OfflineBroker:
        def send_task(self, *a, **k):
            raise ConnectionError("do not expose broker credentials")
    coord.publish(OfflineBroker())
    assert client.get(f"/api/v1/evaluations/{run['id']}").json()["status"] == "queued"
    items = client.get(f"/api/v1/evaluations/{run['id']}/work-items").json()["items"]
    assert all(i["last_error"] == "BROKER_PUBLISH_FAILED" for i in items)


async def test_duplicate_delivery_and_grading_are_idempotent(client, setup_run):
    run = setup_run["post"]("evaluations", setup_run["body"])
    coord = client.app.state.coordinator
    first = ids(client, run["id"])[0]
    await asyncio.gather(coord.process(first), coord.process(first))
    await coord.drain(run["id"])
    await coord.process(first)
    await coord.drain(run["id"])
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["counts"]["passed"] == 2 and report["attempts"] == 2
    assert client.get(f"/api/v1/evaluations/{run['id']}/results?limit=100").json()["total"] == 12


def test_project_capacity_is_shared_across_coordinators(client, setup_run):
    run = setup_run["post"]("evaluations", setup_run["body"])
    coord = client.app.state.coordinator
    other = Coordinator(coord.sessions, coord.settings)
    a, b = ids(client, run["id"])
    assert coord.claim(a)
    assert other.claim(b) is None
    assert other.claim(a) is None


def test_api_restart_does_not_invalidate_live_worker_lease(client, setup_run):
    run = setup_run["post"]("evaluations", setup_run["body"])
    coord = client.app.state.coordinator
    item_id = ids(client, run["id"])[0]
    owner, epoch, _ = coord.claim(item_id)
    replacement = create_app(coord.settings)
    with TestClient(replacement, headers={"Authorization": f"Bearer {TOKEN}"}) as restarted:
        items = restarted.get(f"/api/v1/evaluations/{run['id']}/work-items").json()["items"]
        assert next(i for i in items if i["id"] == item_id)["status"] == "running"
        assert coord.renew(item_id, owner, epoch)


async def test_crash_unknown_effect_blocks_capacity_and_rejects_stale_write(client, setup_run):
    run = setup_run["post"]("evaluations", setup_run["body"])
    coord = client.app.state.coordinator
    first = ids(client, run["id"])[0]
    owner, epoch, _ = coord.claim(first)
    expire(coord, first)
    coord.recover()
    assert not coord.finish(first, owner, epoch, "completed", {"output": "late"})
    items = client.get(f"/api/v1/evaluations/{run['id']}/work-items").json()["items"]
    blocked = next(i for i in items if i["id"] == first)
    assert blocked["status"] == "blocked"
    execution = client.get(f"/api/v1/executions/{blocked['target_id']}").json()
    assert execution["status"] == "outcome_unknown"
    assert execution["late_result"]["response"]["output"] == "late"
    release_url = f"/api/v1/executions/{execution['id']}/release-capacity"
    body = {"reason": "Operator verified the remote operation has stopped"}
    assert client.post(release_url, json=body).status_code == 403
    assert client.post(release_url, json=body, headers={"X-Admin-Token": "test-admin-token"}).status_code == 200
    await coord.drain(run["id"])
    result = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert result["counts"]["unknown"] == 1 and result["counts"]["passed"] == 1
    assert result["success_rate"] == 1 and result["decision_coverage"] == .5
    assert len(client.get(f"/api/v1/evaluations/{run['id']}/audit").json()["items"]) >= 3


async def test_grading_lease_recovery_does_not_reinvoke_agent(client, setup_run):
    run = setup_run["post"]("evaluations", setup_run["body"])
    coord = client.app.state.coordinator
    for item_id in ids(client, run["id"]):
        await coord.process(item_id)
    with coord.sessions() as session:
        item = session.scalar(select(WorkItem).filter_by(kind="grade", target_id=run["grading_batch_id"]))
        grade_id = item.id
    assert coord.claim(grade_id)
    expire(coord, grade_id)
    coord.recover()
    await coord.drain(run["id"])
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["attempts"] == 2 and report["success_rate"] == 1


def test_retry_requires_explicit_read_only_declaration(client, setup_run):
    s = setup_run
    profile = s["post"]("profile-versions", {"project_id": s["project"]["id"], "name": "Retry", "version": "1",
        "evaluators": [{"instance_id": "answer", "id": "exact_match"}], "execution": {"max_retries": 1}})
    response = client.post("/api/v1/evaluations", json={**s["body"], "profile_version_id": profile["id"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RETRY_REQUIRES_READ_ONLY"


async def test_read_only_crash_creates_attempt_not_new_repetition(client, setup_run):
    s = setup_run
    version = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "safe", "configuration": {"plugin_id": "example.order.v1"},
        "effects": "read_only", "capabilities": ["tool_calls", "workflow_steps", "retrieval_results"]})
    profile = s["post"]("profile-versions", {"project_id": s["project"]["id"], "name": "Retry", "version": "1",
        "evaluators": [{"instance_id": "answer", "id": "exact_match"}], "execution": {"max_retries": 1}})
    run = s["post"]("evaluations", {**s["body"], "agent_version_id": version["id"], "profile_version_id": profile["id"]})
    coord = client.app.state.coordinator
    first = ids(client, run["id"])[0]
    coord.claim(first); expire(coord, first); coord.recover()
    await coord.drain(run["id"])
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["planned_trials"] == 2 and report["attempts"] == 3
    assert report["success_rate"] == 1 and report["retry_rate"] == .5


async def test_cancellation_before_dispatch_does_not_invoke_agent(client, setup_run):
    run = setup_run["post"]("evaluations", setup_run["body"])
    client.post(f"/api/v1/evaluations/{run['id']}/cancel")
    await client.app.state.coordinator.drain(run["id"])
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["run_status"] == "cancelled" and report["counts"]["excluded"] == 2
