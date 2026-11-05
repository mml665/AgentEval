"""Current-package recovery drill on NEW isolated Compose projects only.

Does not read .env, reuse projects, remove volumes, or change existing deployments.
All authentication/password values are generated fixture secrets, never printed.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from examples.product_demo import create_product_demo


class AcceptanceError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise AcceptanceError(code)
    return value


def project_name(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,35}-acceptance", value):
        raise argparse.ArgumentTypeError("Use a new lowercase project ending in -acceptance (prefix max 36 chars)")
    return value


def clean_environment(parent):
    return {k: v for k, v in parent.items() if not k.upper().startswith(("AGENTEVAL_", "COMPOSE_", "POSTGRES_"))}


def unpack_source(raw, destination):
    """Only accept regular files/directories from our fixed Git commit."""
    destination = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and "\\" not in member.name
                and ":" not in member.name and (member.isfile() or member.isdir()), "UNSAFE_SOURCE_ARCHIVE")
            require((destination / member.name).resolve().is_relative_to(destination), "UNSAFE_SOURCE_ARCHIVE")
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def file_manifest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()}


def write_private(path, raw):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(raw)


# Runs in the actual image; only counts/hashes leave the database, never payloads.
INVENTORY = """
import json
from sqlalchemy import select, text
from agenteval import __version__
from agenteval.archives import encoded
from agenteval.config import Settings
from agenteval.contracts import digest
from agenteval.db import Base, make_database
engine, _ = make_database(Settings().database_url)
with engine.connect() as conn:
    revision = conn.execute(text('select version_num from alembic_version')).scalar()
    tables = {}
    for table in Base.metadata.sorted_tables:
        rows = [encoded(dict(r)) for r in conn.execute(select(table).order_by(table.c.id)).mappings()]
        tables[table.name] = {'count':len(rows), 'hash':digest(rows)}
print(json.dumps({'version':__version__, 'revision':revision, 'tables':tables}, sort_keys=True))
engine.dispose()
"""


class Stack:
    def __init__(self, runner, name, port, image):
        self.runner, self.name, self.port, self.image = runner, name, port, image
        self.origin = f"http://127.0.0.1:{port}"
        self.override = runner.folder / (name + ".json")
        self.team, self.owned = False, False

    def configure(self):
        runtime = {"AGENTEVAL_LEASE_SECONDS": "3", "AGENTEVAL_AUTH_MODE": "team" if self.team else "legacy",
            "AGENTEVAL_LEGACY_TOKEN_ENABLED": "0", "AGENTEVAL_SESSION_COOKIE_SECURE": "0", "AGENTEVAL_SESSION_ORIGINS": self.origin}
        self.override.write_text(json.dumps({"services": {name: {"image": self.image,
            "build": {"context": str(self.runner.source), "args": {"SOURCE_REVISION": self.runner.commit}},
            "environment": runtime, "restart": "no"} for name in ("api", "worker", "dispatcher")}}), encoding="utf-8")

    def dc(self, *args, **kwargs):
        return self.runner.command(["docker", "compose", "--env-file", str(self.runner.empty_env),
            "-f", str(self.runner.source / "docker-compose.yml"), "-f", str(self.override),
            "-p", self.name, *args], extra={"AGENTEVAL_API_PORT": str(self.port)}, **kwargs)

    def reserve(self):
        for resource, arguments in (("CONTAINER", ["ps", "-aq"]), ("VOLUME", ["volume", "ls", "-q"])):
            existing = self.runner.command(["docker", *arguments, "--filter", "label=com.docker.compose.project=" + self.name])
            require(not existing.strip(), "EXISTING_PROJECT_" + resource)
        self.configure()
        self.owned = True

    def client(self, legacy=True):
        headers = {"Authorization": "Bearer " + self.runner.env["AGENTEVAL_API_TOKEN"]} if legacy else {"Origin": self.origin}
        return httpx.Client(base_url=self.origin, headers=headers, trust_env=False, timeout=15)

    def container(self, service):
        return self.dc("ps", "-aq", service).strip().decode()

    def inventory(self):
        return json.loads(self.dc("run", "--rm", "--no-deps", "-T", "api", "python", "-c", INVENTORY))


class Runner:
    def __init__(self, args, stage):
        self.args, self.stage = args, stage
        self.folder = ROOT / ".build" / (args.project + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        self.folder.mkdir(parents=True, exist_ok=False)
        self.empty_env = stage / "empty.env"
        self.empty_env.write_text("", encoding="utf-8")
        self.env = clean_environment(os.environ)
        self.env.update({"AGENTEVAL_API_TOKEN": secrets.token_urlsafe(32), "POSTGRES_PASSWORD": secrets.token_hex(24),
            "AGENTEVAL_CREDENTIAL_FINGERPRINT_KEY": secrets.token_urlsafe(32), "AGENTEVAL_ARCHIVE_SIGNING_KEY": secrets.token_urlsafe(32),
            "AGENTEVAL_BOOTSTRAP_PASSWORD": secrets.token_urlsafe(32)})
        self.commit = self.command(["git", "rev-parse", "HEAD"]).decode().strip()
        require(not self.command(["git", "status", "--porcelain", "--untracked-files=no"]).strip(), "COMMIT_CHANGES_BEFORE_ACCEPTANCE")
        raw = self.command(["git", "archive", self.commit])
        self.source = stage / "source"
        unpack_source(raw, self.source)
        self.report = {"schema": "deployment-acceptance-v1", "commit": self.commit,
            "source_archive_sha256": hashlib.sha256(raw).hexdigest(), "started_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Isolated fixtures, not real business/Judge/user/capacity acceptance", "checks": [], "status": "running",
            "projects": [args.project, args.project[:-11] + "-restore-acceptance"]}
        self.stacks, self.started = [], time.monotonic()

    def command(self, argv, extra=None, timeout=120, input=None):
        result = subprocess.run(argv, cwd=ROOT, env={**self.env, **(extra or {})}, input=input,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            message = result.stderr.decode(errors="replace")[-2500:]
            for key, value in self.env.items():
                if value and key.startswith(("AGENTEVAL_", "POSTGRES_")): message = message.replace(value, "[REDACTED]")
            raise AcceptanceError("COMMAND_FAILED:" + argv[0] + ":" + message)
        return result.stdout

    @contextmanager
    def step(self, name):
        print("Checking " + name, flush=True)
        started = time.monotonic()
        try:
            yield
        except Exception:
            self.report["failed_step"] = name
            raise
        else:
            self.report["checks"].append({"name": name, "seconds": round(time.monotonic() - started, 3), "status": "passed"})

    def wait(self, predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value: return value
            except (httpx.RequestError, KeyError): pass
            time.sleep(.2)
        raise AcceptanceError("CONDITION_TIMEOUT")

    def post(self, client, path, body):
        response = client.post("/api/v1/" + path, json=body)
        require(response.is_success, f"API_POST_{response.status_code}:" + path)
        return response.json()

    def get(self, client, path):
        response = client.get("/api/v1/" + path)
        require(response.is_success, f"API_GET_{response.status_code}:" + path)
        return response.json()

    def settled(self, client, run_id):
        def completed():
            value = self.get(client, "evaluations/" + run_id)
            return value if value["status"] == "completed" and value["grading_batches"] and all(
                b["status"] == "completed" for b in value["grading_batches"]) else None
        return self.wait(completed, 120)

    def login(self, client, username="acceptance-admin"):
        self.post(client, "auth/login", {"username": username, "password": self.env["AGENTEVAL_BOOTSTRAP_PASSWORD"]})
        client.headers["X-CSRF-Token"] = self.get(client, "auth/session")["csrf_token"]

    def execute(self):
        image = "agenteval-acceptance:" + self.commit[:12] + "-" + secrets.token_hex(3)
        source = Stack(self, self.args.project, self.args.port or free_port(), image)
        recovered = Stack(self, self.report["projects"][1], free_port(), image)
        while recovered.port == source.port:
            recovered = Stack(self, self.report["projects"][1], free_port(), image)
        self.stacks = [source, recovered]
        with self.step("reserve_new_projects"):
            source.reserve(); recovered.reserve()
        with self.step("build_current_package"):
            source.dc("build", "api", timeout=1200)
            self.report["image_id"] = self.command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).decode().strip()
            self.report["docker_version"] = self.command(["docker", "version", "--format", "{{.Server.Version}}"]).decode().strip()
        with source.client() as client:
            with self.step("start_and_migrate_all_services"):
                source.dc("up", "-d", "--no-build", timeout=180)
                self.wait(lambda: client.get("/ready").status_code == 200)
                require(client.get("/").status_code == 200, "FRONTEND_UNAVAILABLE")
                source.dc("exec", "-T", "api", "python", "-m", "agenteval", "init-db")
            with self.step("queued_work_survives_postgres_redis_api_restart"):
                queued = self.queue_restart(source, client)
            with self.step("worker_sigkill_preserves_unknown_without_reexecution"):
                self.worker_fault(source, client, queued)
            with self.step("live_database_broker_outages_degrade_and_recover_readiness"):
                for dependency in ("redis", "db"):
                    source.dc("stop", dependency)
                    require(client.get("/health").status_code == 200, "LIVENESS_LOST:" + dependency)
                    self.wait(lambda: client.get("/ready").status_code == 503)
                    source.dc("start", dependency)
                    self.wait(lambda: client.get("/ready").status_code == 200)
            with self.step("failure_issue_fix_regression_release_fixed_evidence"):
                demo = create_product_demo(client)
                self.report["product_fixture"] = demo
                snapshots = {key: self.get(client, "release-decisions/" + value)["snapshot"] for key, value in demo["decisions"].items()}
                require(self.get(client, "quality/issues/" + demo["issue_id"])["status"] == "resolved", "ISSUE_NOT_RESOLVED")
            with self.step("team_mode_project_roles_and_private_draft"):
                self.team_checks(source, client, demo, queued)
            with self.step("quiesced_database_and_environment_backup"):
                source.dc("stop", "worker", "dispatcher", "api")
                before = source.inventory()
                self.report["database_before"] = before
                dump = source.dc("exec", "-T", "db", "pg_dump", "-U", "agenteval", "-d", "agenteval", "-Fc")
                write_private(self.folder / "database.dump", dump)
                environment = self.folder / "environments"
                environment.mkdir()
                self.command(["docker", "cp", source.container("api") + ":/app/data/environments/.", str(environment)])
                files = file_manifest(environment)
                require(files, "ENVIRONMENT_BACKUP_EMPTY")
                self.report["backup"] = {"database_sha256": hashlib.sha256(dump).hexdigest(), "bytes": len(dump), "environment_files": files}
            with self.step("restore_new_database_environment_and_fixed_reports"):
                recovered.team = True; recovered.configure()
                recovered.dc("up", "-d", "db", "redis", timeout=180)
                recovered.dc("exec", "-T", "db", "pg_restore", "-U", "agenteval", "-d", "agenteval", "--exit-on-error", input=dump)
                recovered.dc("run", "--rm", "--no-deps", "-T", "api", "python", "-m", "agenteval", "init-db")
                after = recovered.inventory()
                require(after == before, "DATABASE_ROUNDTRIP_MISMATCH")
                recovered.dc("create", "--no-build", "api")
                self.command(["docker", "cp", str(environment) + "/.", recovered.container("api") + ":/app/data/environments/"])
                recovered.dc("run", "--rm", "--no-deps", "-T", "--user", "root", "api", "chown", "-R", "agenteval:agenteval", "/app/data/environments")
                verified = self.folder / "restored-environments"
                verified.mkdir()
                self.command(["docker", "cp", recovered.container("api") + ":/app/data/environments/.", str(verified)])
                require(file_manifest(verified) == files, "ENVIRONMENT_ROUNDTRIP_MISMATCH")
                self.report["database_after_restore"] = after
                recovered.dc("up", "-d", "--no-build", timeout=180)
                self.verify_recovery(recovered, demo, snapshots)
            if self.args.rollback_ref:
                with self.step("prior_package_rollback_without_database_downgrade"):
                    self.rollback(recovered, before, image, demo, snapshots)
            else:
                self.report["rollback"] = {"status": "not_run", "reason": "Supply --rollback-ref"}

    def queue_restart(self, stack, client):
        stack.dc("stop", "worker", "dispatcher")
        queued = self.post(client, "onboarding/evaluations", {"request_key": secrets.token_hex(16),
            "project_name": "Compose durable queue fixture", "agent_name": "Actual LangGraph fixture", "adapter_type": "langgraph",
            "agent": {"version": "1", "configuration": {"plugin_id": "example.order.graph"}}, "goals": ["answer"],
            "benchmark": {"version": "1", "cases": [{"case_key": "one", "input": {"order_id": "1001"},
                "assertions": {"expected_output": {"order_id": "1001", "status": "shipped"}}}]}})
        require(self.get(client, "evaluations/" + queued["id"])["status"] == "queued", "NOT_QUEUED")
        stack.dc("restart", "db", "redis")
        stack.dc("restart", "api")
        self.wait(lambda: client.get("/health").status_code == 200)
        require(self.get(client, "evaluations/" + queued["id"])["status"] == "queued", "QUEUE_LOST")
        stack.dc("start", "worker", "dispatcher")
        self.settled(client, queued["id"])
        require(self.get(client, f"evaluations/{queued['id']}/report")["success_rate"] == 1, "QUEUE_RESULT_CHANGED")
        return queued

    def worker_fault(self, stack, client, queued):
        agent = self.post(client, "agents", {"project_id": queued["project_id"], "name": "Crash fixture", "adapter_type": "python"})
        version = self.post(client, f"agents/{agent['id']}/versions", {"version": "1", "configuration": {"plugin_id": "example.slow"}})
        profile = self.post(client, "profile-versions", {"project_id": queued["project_id"], "name": "Crash grading", "version": "1",
            "evaluators": [{"instance_id": "answer", "id": "exact_match"}], "execution": {"timeout_seconds": 30}})
        run = self.post(client, "evaluations", {"agent_version_id": version["id"], "benchmark_version_id": queued["benchmark_version_id"], "profile_version_id": profile["id"]})
        item = self.wait(lambda: next((i for i in self.get(client, f"evaluations/{run['id']}/work-items")["items"]
            if i["kind"] == "execute" and i["status"] == "running"), None))
        self.wait(lambda: self.get(client, f"executions/{item['target_id']}/trace")["spans"])
        stack.dc("kill", "-s", "SIGKILL", "worker")
        self.wait(lambda: any(i["status"] == "blocked" for i in self.get(client, f"evaluations/{run['id']}/work-items")["items"]))
        self.wait(lambda: client.get("/ready").status_code == 503)
        stack.dc("start", "worker")
        self.settled(client, run["id"])
        report = self.get(client, f"evaluations/{run['id']}/report")
        require(report["counts"]["unknown"] == 1 and report["attempts"] == 1, "UNKNOWN_REEXECUTED_OR_PASSED")
        require(self.get(client, f"executions/{item['target_id']}/trace")["spans"], "CRASH_TRACE_LOST")
        self.report["worker_fault"] = {"run_id": run["id"], "unknown": 1, "attempts": 1, "capacity": "blocked"}

    def team_checks(self, stack, client, demo, queued):
        stack.team = True; stack.configure()
        stack.dc("up", "-d", "--no-build", "api", "worker", "dispatcher", timeout=180)
        stack.dc("exec", "-T", "-e", "AGENTEVAL_BOOTSTRAP_PASSWORD", "api", "python", "-m", "agenteval", "init-admin", "--username", "acceptance-admin")
        self.wait(lambda: client.get("/ready").status_code == 200)
        require(client.get("/api/v1/projects").status_code == 401, "LEGACY_NOT_DISABLED")
        with stack.client(False) as admin, stack.client(False) as reader:
            self.login(admin)
            user = self.post(admin, "team/users", {"username": "acceptance-reader", "display_name": "Read only fixture", "password": self.env["AGENTEVAL_BOOTSTRAP_PASSWORD"]})
            self.post(admin, "team/members/" + demo["project_id"], {"user_id": user["id"], "role": "viewer"})
            self.login(reader, "acceptance-reader")
            require(reader.get("/api/v1/evaluations/" + demo["fixed_run_id"]).status_code == 200, "READER_RESULT_DENIED")
            require(reader.get("/api/v1/evaluations/" + queued["id"]).status_code == 404, "CROSS_PROJECT_LEAK")
            require(reader.post("/api/v1/agents", json={"project_id": demo["project_id"], "name": "deny", "adapter_type": "python"}).status_code == 403, "READER_WRITE_ALLOWED")
            self.post(admin, "onboarding/drafts/" + demo["project_id"], {"payload": {
                "request_key": secrets.token_hex(16), "cases": "private acceptance draft"}})
            require(self.get(reader, "onboarding/drafts/" + demo["project_id"])["payload"] is None, "PRIVATE_DRAFT_LEAK")

    def verify_recovery(self, stack, demo, snapshots):
        with stack.client(False) as admin, stack.client(False) as reader:
            self.wait(lambda: admin.get("/ready").status_code == 200)
            self.login(admin); self.login(reader, "acceptance-reader")
            for key, value in demo["decisions"].items():
                require(self.get(admin, "release-decisions/" + value)["snapshot"] == snapshots[key], "RELEASE_SNAPSHOT_CHANGED")
                require(admin.get(f"/api/v1/release-decisions/{value}/export?format=html").status_code == 200, "RESTORED_EXPORT_FAILED")
            require(self.get(admin, "quality/issues/" + demo["issue_id"])["status"] == "resolved", "RESTORED_ISSUE_CHANGED")
            require(self.get(admin, "quality/baselines/" + demo["project_id"])["items"], "BASELINE_LOST")
            require(self.get(admin, "onboarding/drafts/" + demo["project_id"])["payload"]["cases"] == "private acceptance draft", "OWNER_DRAFT_LOST")
            require(self.get(reader, "onboarding/drafts/" + demo["project_id"])["payload"] is None, "RESTORED_DRAFT_LEAK")
            require(reader.post("/api/v1/agents", json={"project_id": demo["project_id"], "name": "deny", "adapter_type": "python"}).status_code == 403, "RESTORED_ROLE_CHANGED")
            with stack.client() as legacy:
                require(legacy.get("/api/v1/projects").status_code == 401, "RESTORED_LEGACY_ENABLED")

    def rollback(self, stack, before, image, demo, snapshots):
        ref = self.command(["git", "rev-parse", "--verify", self.args.rollback_ref + "^{commit}"]).decode().strip()
        require(ref != self.commit, "ROLLBACK_REQUIRES_DIFFERENT_COMMIT")
        previous = self.stage / "previous"
        unpack_source(self.command(["git", "archive", ref]), previous)
        previous_image = image + "-previous"
        self.command(["docker", "build", "-t", previous_image, str(previous)], timeout=1200)
        stack.dc("stop", "worker", "dispatcher", "api")
        stack.image = previous_image; stack.configure()
        old = stack.inventory()
        require(old["revision"] == before["revision"] and old["tables"].keys() == before["tables"].keys(), "ROLLBACK_SCHEMA_INCOMPATIBLE")
        stack.dc("up", "-d", "--no-build", timeout=180)
        self.verify_recovery(stack, demo, snapshots)
        self.report["rollback"] = {"commit": ref, "version": old["version"], "database_revision": old["revision"],
            "image_id": self.command(["docker", "image", "inspect", previous_image, "--format", "{{.Id}}"]).decode().strip(),
            "strategy": "Compatible code rollback on restored copy, no schema downgrade"}

    def finish(self, error=None):
        cleanup_failed = False
        for stack in self.stacks:
            if stack.owned:
                try: stack.dc("stop", timeout=90)
                except Exception: cleanup_failed = True
        self.report.update(status="failed" if error or cleanup_failed else "passed", finished_at=datetime.now(timezone.utc).isoformat(),
            seconds=round(time.monotonic() - self.started, 3), containers_stopped=not cleanup_failed, volumes_preserved=True)
        if error: self.report["error_type"] = type(error).__name__
        write_private(self.folder / "report.json", json.dumps(self.report, ensure_ascii=False, indent=2).encode())
        print(json.dumps({"status": self.report["status"], "report": str(self.folder / "report.json"), "checks": len(self.report["checks"]), "volumes_preserved": True}), flush=True)
        return self.report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=project_name)
    parser.add_argument("--port", type=int)
    parser.add_argument("--rollback-ref", help="Compatible prior commit to build and verify on the restored copy")
    args = parser.parse_args()
    if args.port is not None and not 1024 <= args.port <= 65535: parser.error("Use a free loopback port from 1024 to 65535")
    error = None
    with tempfile.TemporaryDirectory(prefix="agenteval-compose-") as directory:
        runner = Runner(args, Path(directory))
        try:
            runner.execute()
        except Exception as exc:
            error = exc
            print("Acceptance failed: " + (str(exc) if isinstance(exc, AcceptanceError) else type(exc).__name__), file=sys.stderr)
        passed = runner.finish(error)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
