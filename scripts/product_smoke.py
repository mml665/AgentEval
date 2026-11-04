"""Exercise the complete product loop on an isolated database through public APIs."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from fastapi.testclient import TestClient
from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.db import Base
from examples.product_demo import create_product_demo


def main():
    with tempfile.TemporaryDirectory(prefix="agenteval-product-") as folder:
        token = secrets.token_urlsafe(32)
        app = create_app(Settings(database_url=os.getenv("AGENTEVAL_PRODUCT_DATABASE_URL", f"sqlite:///{Path(folder).as_posix()}/product.db"), api_token=token,
            execution_mode="local", environment_root=str(Path(folder) / "environments")))
        Base.metadata.create_all(app.state.engine)
        with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
            value = create_product_demo(client)
            def post(path, body):
                result = client.post("/api/v1/" + path, json=body)
                result.raise_for_status()
                return result.json()
            fixed = client.get(f"/api/v1/evaluations/{value['fixed_run_id']}").json()
            baseline = client.get(f"/api/v1/evaluations/{value['baseline_run_id']}").json()
            versions = client.get(f"/api/v1/quality/agent-versions?project_id={value['project_id']}").json()["items"]
            agent_id = next(v["agent_id"] for v in versions if v["id"] == fixed["agent_version_id"])
            params = {"agent_id": agent_id, "candidate_version_id": fixed["agent_version_id"],
                "baseline_version_id": baseline["agent_version_id"], "benchmark_version_id": fixed["benchmark_version_id"]}
            # Same project and identical cases, but an independent Agent with an open blocker.
            other = post("agents", {"project_id": value["project_id"], "name": "Separate identity", "adapter_type": "python"})
            av = post(f"agents/{other['id']}/versions", {"version": "1", "configuration": {"plugin_id": "example.refund.liar"}})
            bad = post("evaluations", {"agent_version_id": av["id"], "benchmark_version_id": fixed["benchmark_version_id"], "profile_version_id": fixed["profile_version_id"]})
            bad = client.get(f"/api/v1/evaluations/{bad['id']}").json()
            issue = client.get(f"/api/v1/quality/issues?project_id={value['project_id']}").json()["items"][0]
            assert issue["kind"] == "anomaly" and issue["issue_scope"]["agent_id"] == other["id"]
            post(f"quality/issues/{issue['id']}/update", {"expected_revision": issue["revision"], "status": "confirmed", "note": "Confirmed independent Agent failure"})
            comparison = post("regressions/compare", {"baseline_batch_id": bad["grading_batches"][0]["id"], "candidate_batch_id": bad["grading_batches"][0]["id"]})
            blocked = post("release-decisions", {"comparison_id": comparison["id"]})
            assert blocked["status"] == "blocked" and blocked["snapshot"]["blocking_issues"][0]["id"] == issue["id"]
            summary = client.get(f"/api/v1/quality/workbench/{value['project_id']}", params=params).json()
            assert summary["open_issues"] == 0 and summary["candidate"]["success_rate"] == 1
            assert summary["latest_decision"]["status"] == "ready"
            tasks = client.get("/api/v1/evaluations", params={"project_id": value["project_id"], "q": fixed["id"], "quality": "passed"}).json()
            assert tasks["total"] == 1 and tasks["items"][0]["quality_summary"]["quality_status"] == "passed"
            origin_issue = client.get(f"/api/v1/quality/issues/{value['issue_id']}").json()
            facts = client.get(f"/api/v1/executions/{origin_issue['execution_id']}/evidence-summary",
                params={"grading_batch_id": origin_issue["batch_id"]}).json()
            assert facts["items"][0]["rule"] == "state_assertion" and facts["items"][0]["verdict"] == "failed"
            assert client.get("/api/v1/quality/issues", params={"project_id": value["project_id"], **params}).json()["total"] == 0
            assert post("release-decisions", {"comparison_id": summary["comparison"]["id"]})["status"] == "ready"
            assert client.get(f"/api/v1/quality/project-overview/{value['project_id']}").json()["open_issues"] == 1
            for state, decision_id in value["decisions"].items():
                response = client.get(f"/api/v1/release-decisions/{decision_id}/export")
                assert response.status_code == 200 and response.json()["status"] == state
            output = ROOT / ".build/product-smoke.json"
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps({"workflow": value, "release_states": list(value["decisions"]),
                "scope": "Local fixtures; not real business Agent acceptance or user productivity measurements"}, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Product smoke passed: guided run -> facts -> repair -> compatible retest -> regression -> blocked/ready/insufficient release snapshots")


if __name__ == "__main__":
    main()
