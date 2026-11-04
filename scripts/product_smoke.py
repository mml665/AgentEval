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
            summary = client.get(f"/api/v1/quality/workbench/{value['project_id']}").json()
            assert summary["open_issues"] == 0 and summary["candidate"]["success_rate"] == 1
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
