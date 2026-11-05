"""Display/export protection and signed project recovery across SQLite/PostgreSQL."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.contracts import digest
from agenteval.db import make_database
from agenteval import archives
from examples.product_demo import create_product_demo


def main():
    with tempfile.TemporaryDirectory(prefix="agenteval-protection-") as folder:
        root = Path(folder)
        url = os.getenv("AGENTEVAL_PROTECTION_DATABASE_URL", "sqlite:///" + (root / "source.db").as_posix())
        def migrate(db):
            cfg = Config(str(ROOT / "alembic.ini")); cfg.attributes["database_url"] = db; command.upgrade(cfg, "head")
        migrate(url)
        secret, token = "fixture-contact-" + secrets.token_hex(16), secrets.token_urlsafe(32)
        os.environ["AGENTEVAL_REDACTION_SECRET_SMOKE"] = secret
        os.environ["AGENTEVAL_ARCHIVE_SIGNING_KEY"] = secrets.token_urlsafe(32)
        app = create_app(Settings(database_url=url, api_token=token, auth_mode="legacy", execution_mode="local",
            credential_fingerprint_key=secrets.token_urlsafe(32), environment_root=str(root / "environments")))
        with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
            def post(path, body):
                response = client.post("/api/v1/" + path, json=body); response.raise_for_status(); return response.json()
            demo = create_product_demo(client)
            post("protection/policy/" + demo["project_id"], {"secret_env_names": ["AGENTEVAL_REDACTION_SECRET_SMOKE"], "retention_days": 30})
            decision = client.get("/api/v1/release-decisions/" + demo["decisions"]["ready"]).json()
            created = post("release-decisions", {"comparison_id": decision["comparison_id"], "note": "Contact context " + secret})
            assert secret not in json.dumps(created)
            for fmt in ("json", "html"):
                exported = client.get(f"/api/v1/release-decisions/{created['id']}/export?format={fmt}")
                assert exported.status_code == 200 and secret not in exported.text
                if fmt == "json":
                    value = exported.json(); checksum = value.pop("content_hash"); assert digest(value) == checksum
            assert client.post(f"/api/v1/protection/evaluations/{demo['fixed_run_id']}/raw", json={"reason": "Shared identities must not read originals"}).status_code == 403
            inventory = client.get("/api/v1/protection/storage/" + demo["project_id"]).json()
            assert inventory["release_referenced_runs"] >= 2 and not inventory["automatic_deletion"]
            artifact = root / "snapshot.json"
            manifest = archives.create(app.state.engine, demo["project_id"], artifact, "Isolated current archive recovery smoke")
            assert archives.verify(artifact)["manifest"] == manifest
            restored_url = "sqlite:///" + (root / "restore.db").as_posix(); migrate(restored_url)
            restored, _ = make_database(restored_url)
            try:
                archives.restore(restored, artifact, True)
                actual = archives.snapshot(restored, demo["project_id"])
                assert secret in json.dumps(actual)  # Original evidence was retained, while ordinary export was redacted.
                assert len(actual["release_decisions"]) == manifest["counts"]["release_decisions"]
            finally: restored.dispose()
        report = ROOT / ".build/protection-smoke.json"; report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps({"source_database": app.state.engine.dialect.name, "restore_database": "sqlite",
            "checks": ["versioned_policy", "release_json_html_redacted", "derived_hash", "legacy_raw_denied", "release_reference_inventory", "signed_snapshot", "isolated_restore", "original_content_retained"],
            "scope": "Non-destructive integration recovery, not a full current Compose disaster-recovery acceptance"},indent=2),encoding="utf-8")
        print("Protection smoke passed: derived exports, original access denial, signed project snapshot and isolated evidence recovery")


if __name__ == "__main__": main()
