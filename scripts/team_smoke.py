"""Public-API team smoke on a fresh SQLite or explicitly dedicated PostgreSQL database."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.security import bootstrap_admin

ORIGIN = "http://127.0.0.1"


def main():
    with tempfile.TemporaryDirectory(prefix="agenteval-team-") as folder:
        url = os.getenv("AGENTEVAL_TEAM_DATABASE_URL", "sqlite:///" + (Path(folder) / "team.db").as_posix())
        migration = Config(str(ROOT / "alembic.ini")); migration.attributes["database_url"] = url
        command.upgrade(migration, "head")
        app = create_app(Settings(database_url=url, auth_mode="team", legacy_token_enabled=False,
            api_token="disabled-smoke-legacy-token", session_cookie_secure=False, session_origins=(ORIGIN,),
            environment_root=str(Path(folder) / "environments"), execution_mode="local"))
        suffix, password = secrets.token_hex(4), secrets.token_urlsafe(32)
        # Refuse to take over an existing team. CI supplies an ephemeral database with no accounts.
        bootstrap_admin(app.state.sessions, "admin" + suffix, password)
        with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as admin:
            def post(client, path, body=None):
                response = client.post("/api/v1/" + path, json=body)
                assert response.status_code in (200, 201, 202), (path, response.status_code)
                return response.json()
            def login(client, username):
                post(client, "auth/login", {"username": username, "password": password})
                current = client.get("/api/v1/auth/session").json()
                client.headers["X-CSRF-Token"] = current["csrf_token"]
                return current
            login(admin, "admin" + suffix)
            a = post(admin, "projects", {"name": "Team smoke A " + suffix})
            b = post(admin, "projects", {"name": "Team smoke B " + suffix})
            users = {}
            for name, role in (("writer", "evaluator"), ("reader", "viewer")):
                user = post(admin, "team/users", {"username": name + suffix, "password": password, "display_name": name})
                post(admin, "team/members/" + a["id"], {"user_id": user["id"], "role": role})
                users[name] = user
            writer = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
            reader = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
            with closing(writer), closing(reader):
                identity = login(writer, "writer" + suffix); login(reader, "reader" + suffix)
                assert writer.get("/api/v1/projects").json()["total"] == 1
                assert writer.get("/api/v1/workspace/" + b["id"]).status_code == 404
                assert reader.post("/api/v1/agents", json={"project_id": a["id"], "name": "deny", "adapter_type": "python"}).status_code == 403
                agent = post(writer, "agents", {"project_id": a["id"], "name": "Team fixture", "adapter_type": "python"})
                av = post(writer, f"agents/{agent['id']}/versions", {"version": "1", "configuration": {"plugin_id": "example.order.v2"}})
                bm = post(writer, "benchmarks", {"project_id": a["id"], "name": "Team fixture"})
                bv = post(writer, f"benchmarks/{bm['id']}/versions", {"version": "1", "cases": [{"case_key": "one", "input": {"order_id": "1001"}, "assertions": {"expected_output": {"order_id": "1001", "status": "shipped"}}}]})
                profile = post(writer, "profile-versions", {"project_id": a["id"], "name": "Rules", "version": "1", "evaluators": [{"instance_id": "answer", "id": "exact_match"}]})
                body = {"agent_version_id": av["id"], "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]}
                run = post(writer, "evaluations", body)
                actual = writer.get("/api/v1/evaluations/" + run["id"]).json()
                assert actual["quality_summary"]["initiator"]["id"] == identity["id"]
                assert actual["status"] == "completed"
                assert reader.get(f"/api/v1/evaluations/{run['id']}/export", params={"grading_batch_id": run["grading_batch_id"]}).status_code == 200
                raw_path = f"protection/evaluations/{run['id']}/raw"
                assert reader.post("/api/v1/" + raw_path, json={"reason": "Controlled smoke original inspection"}).status_code == 403
                post(admin, "protection/grants/" + a["id"], {"user_id": users["reader"]["id"], "raw_read": True})
                original = post(reader, raw_path, {"reason": "Controlled smoke original inspection"})
                assert original["view"]["kind"] == "raw-audited"
                post(admin, "protection/grants/" + a["id"], {"user_id": users["reader"]["id"], "raw_read": False})
                assert reader.post("/api/v1/" + raw_path, json={"reason": "Controlled smoke original inspection"}).status_code == 403
                issue = writer.get("/api/v1/quality/issues", params={"project_id": a["id"]}).json()["items"][0]
                updated = post(writer, f"quality/issues/{issue['id']}/update", {"expected_revision": issue["revision"], "status": "confirmed", "assignee_user_id": identity["id"], "note": "Fixture failure confirmed"})
                assert updated["assignee_user_id"] == identity["id"]
                from uuid import uuid4
                request_key=str(uuid4())
                reuse={**body,"project_id":a["id"],"request_key":request_key}
                assert post(writer,"onboarding/reuse-preview",reuse)["planned_trials"]==1
                rerun=post(writer,"onboarding/reuse",reuse)
                assert post(writer,"onboarding/reuse",reuse)["id"]==rerun["id"]
                assert writer.get(f"/api/v1/onboarding/requests/{request_key}",params={"project_id":a["id"]}).json()["run"]["id"]==rerun["id"]
                post(writer,"quality/baselines/"+a["id"],{"agent_id":agent["id"],"benchmark_version_id":bv["id"],"grading_batch_id":run["grading_batch_id"]})
                assert writer.get("/api/v1/quality/baselines/"+a["id"]).json()["items"][0]["grading_batch_id"]==run["grading_batch_id"]
                private={"mode":"reuse","request_key":request_key,"cases":'[{"password":"private-author-fixture"}]',"agent_version_id":av["id"],"benchmark_version_id":bv["id"],"profile_version_id":profile["id"]}
                post(writer,"onboarding/drafts/"+a["id"],{"payload":private})
                assert writer.get("/api/v1/onboarding/drafts/"+a["id"]).json()["payload"]["cases"]==private["cases"]
                assert reader.get("/api/v1/onboarding/drafts/"+a["id"]).json()["payload"] is None
                document=json.dumps([{"case_key":"one","input":{"nested":[1,{"value":True}]},"assertions":{"expected_output":"ok"}}])
                assert post(writer,f"benchmarks/{bm['id']}/preview",{"document":document})["valid"]
                v2=post(writer,f"benchmarks/{bm['id']}/publish",{"version":"2","document":document})
                assert writer.get(f"/api/v1/benchmark-versions/{bv['id']}/diff",params={"other_version_id":v2["id"]}).json()["modified"]
                comparison=post(writer,"regressions/compare",{"baseline_batch_id":run["grading_batch_id"],"candidate_batch_id":run["grading_batch_id"]})
                decision=post(writer,"release-decisions",{"comparison_id":comparison["id"]})
                frozen=decision["snapshot"]
                post(writer,f"evaluations/{run['id']}/regrade",{})
                current=writer.get("/api/v1/release-decisions/"+decision["id"]).json()
                assert current["snapshot"]==frozen and not current["current_validity"]["current"]
                token = post(admin, "team/machines", {"name": "CI smoke", "project_ids": [a["id"]], "permission": "evaluate", "expires_in_seconds": 600})
                with TestClient(app, headers={"Authorization": "Bearer " + token["token"]}) as machine:
                    machine_run = post(machine, "evaluations", body)
                    record = machine.get("/api/v1/evaluations/" + machine_run["id"]).json()
                    assert record["quality_summary"]["initiator"]["id"] == token["id"]
                    assert machine.get("/api/v1/workspace/" + b["id"]).status_code == 404
                    post(admin, f"team/machines/{token['id']}/revoke")
                    assert machine.get("/api/v1/projects").status_code == 401
                post(admin, "team/members/" + a["id"], {"user_id": identity["id"], "role": "viewer"})
                assert writer.post("/api/v1/evaluations", json=body).status_code == 403
                post(writer, "auth/logout")
                assert writer.get("/api/v1/projects").status_code == 401
                audits = admin.get("/api/v1/team/audit", params={"limit": 100}).json()["items"]
                assert any(row["actor"]["id"] == identity["id"] and row["status"] == 200 for row in audits)
                assert password not in json.dumps(audits) and token["token"] not in json.dumps(audits)
            artifact = ROOT / ".build/team-smoke.json"; artifact.parent.mkdir(exist_ok=True)
            artifact.write_text(json.dumps({"database": app.state.engine.dialect.name,
                "checks": ["personal_session", "project_isolation", "viewer_denied", "member_assignment", "run_actor", "machine_actor", "machine_revoked", "live_role_change", "logout", "audit", "reuse_idempotency", "explicit_baseline", "private_draft", "case_publish_diff", "release_validity"],
                "scope": "Controlled integration fixtures; not real business or current Compose recovery acceptance"}, indent=2), encoding="utf-8")
            print("Team smoke passed: sessions, project roles, scoped machine tokens, actual actors, audit and revocation")


if __name__ == "__main__":
    main()
