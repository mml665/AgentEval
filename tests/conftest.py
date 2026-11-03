import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.db import Base
from examples.demo import CAPABILITIES, EVALUATORS

TOKEN = "test-token-for-local-tests-only"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", api_token=TOKEN))
    Base.metadata.create_all(app.state.engine)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def setup_run(client):
    def post(path, body):
        response = client.post("/api/v1/" + path, json=body)
        assert response.status_code in (200, 201, 202), response.text
        return response.json()

    project = post("projects", {"name": "Test"})
    agent = post("agents", {"project_id": project["id"], "name": "Orders", "adapter_type": "python"})
    version = post(f"agents/{agent['id']}/versions", {"version": "1.0", "configuration": {"plugin_id": "example.order.v1"}, "capabilities": CAPABILITIES})
    benchmark = post("benchmarks", {"project_id": project["id"], "name": "Cases"})
    data = json.loads(Path("benchmarks/orders.json").read_text(encoding="utf-8"))
    benchmark_version = post(f"benchmarks/{benchmark['id']}/versions", data)
    profile = post("profile-versions", {"project_id": project["id"], "name": "Rules", "version": "1.0", "evaluators": EVALUATORS})
    body = {"agent_version_id": version["id"], "benchmark_version_id": benchmark_version["id"], "profile_version_id": profile["id"]}
    return {"post": post, "project": project, "agent": agent, "version": version,
            "benchmark": benchmark, "benchmark_version": benchmark_version, "profile": profile,
            "body": body, "data": data}

