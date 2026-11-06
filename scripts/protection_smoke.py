"""Display/export protection and signed project recovery across SQLite/PostgreSQL."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
from statistics import median
import sys
import tempfile
from time import perf_counter
from uuid import uuid4
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.contracts import digest
from agenteval.db import Execution, Run, TraceSpan, Trial, make_database
from agenteval import archives
from examples.product_demo import create_product_demo


def exercise_inventory(app, client, demo, base_inventory, samples):
    with app.state.sessions() as session:
        source = session.get(Run, demo["fixed_run_id"])
        case_id = session.scalar(select(Trial.case_id).where(Trial.run_id == source.id).limit(1))
        timestamp = datetime.now(timezone.utc) - timedelta(days=31)
        runs, trials, executions, spans = [], [], [], []
        response = {"output": {"fixture": "x" * 1024}}
        for _ in range(samples):
            run_id, trial_id, execution_id, trace_id = (str(uuid4()) for _ in range(4))
            runs.append({"id": run_id, "created_at": timestamp, "project_id": source.project_id,
                "agent_version_id": source.agent_version_id, "benchmark_version_id": source.benchmark_version_id,
                "profile_version_id": source.profile_version_id, "status": "completed", "manifest": {}, "manifest_hash": "inventory-fixture"})
            trials.append({"id": trial_id, "run_id": run_id, "case_id": case_id, "repetition_index": 0})
            executions.append({"id": execution_id, "trial_id": trial_id, "trace_id": trace_id, "status": "completed", "response": response})
            spans.append({"execution_id": execution_id, "trace_id": trace_id, "span_id": "fixture", "record": {"type": "fixture"}})
        for table, rows in ((Run, runs), (Trial, trials), (Execution, executions), (TraceSpan, spans)):
            session.execute(insert(table.__table__), rows)
        session.commit()
    endpoint = "/api/v1/protection/storage/" + demo["project_id"]
    timings = []
    for _ in range(5):
        started = perf_counter()
        reply = client.get(endpoint)
        timings.append((perf_counter() - started) * 1000)
        reply.raise_for_status()
        inventory = reply.json()
        assert inventory["runs"] == base_inventory["runs"] + samples
        assert inventory["past_retention_runs"] == samples
        assert inventory["release_referenced_runs"] == base_inventory["release_referenced_runs"]
        assert inventory["evidence_json_bytes_by_kind"]["responses"] >= 1024 * samples
        assert inventory["warning_reached"] and len(reply.content) < 4096
    review = client.get(endpoint + "/runs", params={"limit": 25, "offset": max(0, samples - 25)})
    review.raise_for_status()
    assert review.json()["total"] == samples and len(review.json()["items"]) == min(25, samples)
    return {"fixture_runs": samples, "bytes_per_fixture_response_value": 1024,
        "evidence_json_bytes": inventory["evidence_json_bytes"], "summary_response_bytes": len(reply.content),
        "summary_requests": len(timings), "summary_median_ms": round(median(timings), 2), "summary_max_ms": round(max(timings), 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-runs", type=int, default=0, help="Use a temporary SQLite database and add synthetic history after archive verification")
    args = parser.parse_args()
    if not 0 <= args.inventory_runs <= 10000:
        parser.error("--inventory-runs must be between 0 and 10000")
    capacity = None
    with tempfile.TemporaryDirectory(prefix="agenteval-protection-") as folder:
        root = Path(folder)
        temporary_url = "sqlite:///" + (root / "source.db").as_posix()
        url = temporary_url if args.inventory_runs else os.getenv("AGENTEVAL_PROTECTION_DATABASE_URL", temporary_url)
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
            post("protection/policy/" + demo["project_id"], {"secret_env_names": ["AGENTEVAL_REDACTION_SECRET_SMOKE"],
                "retention_days": 30, "evidence_warning_bytes": 1})
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
            assert inventory["warning_reached"] and all(inventory["evidence_json_bytes_by_kind"].values())
            review = client.get("/api/v1/protection/storage/" + demo["project_id"] + "/runs?review=referenced&limit=1")
            review.raise_for_status()
            assert review.json()["total"] == inventory["release_referenced_runs"] and review.json()["items"][0]["release_referenced"]
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
                assert actual["protection_policies"][0]["definition"]["evidence_warning_bytes"] == 1
            finally: restored.dispose()
            if args.inventory_runs:
                capacity = exercise_inventory(app, client, demo, inventory, args.inventory_runs)
        report = ROOT / ".build/protection-smoke.json"; report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps({"source_database": app.state.engine.dialect.name, "restore_database": "sqlite",
            "checks": ["versioned_policy", "release_json_html_redacted", "derived_hash", "legacy_raw_denied", "release_reference_inventory", "capacity_threshold", "signed_snapshot", "isolated_restore", "original_content_retained"],
            "inventory_exercise": capacity,
            "scope": "Non-destructive integration recovery, not a full current Compose disaster-recovery acceptance"},indent=2),encoding="utf-8")
        print("Protection smoke passed: derived exports, original access denial, signed project snapshot and isolated evidence recovery")
        if capacity:
            print(json.dumps(capacity))


if __name__ == "__main__": main()
