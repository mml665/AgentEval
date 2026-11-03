"""Real Celery processes; real Redis in CI, explicit protocol fixture locally."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from smoke import ROOT, free_port


def main():
    (ROOT / ".build").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="queue-", dir=ROOT / ".build"))
    redis_fixture = None
    broker = os.environ.get("AGENTEVAL_TEST_BROKER_URL")
    if not broker:
        from fakeredis import TcpFakeServer
        redis_fixture = TcpFakeServer(("127.0.0.1", 0))
        threading.Thread(target=redis_fixture.serve_forever, daemon=True).start()
        broker = f"redis://127.0.0.1:{redis_fixture.server_address[1]}/0"

    class JudgeHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.loads(request["messages"][1]["content"])
            decision = {"score": float(data["answer"] == data["reference"]),
                        "explanation": "Protocol fixture; no real model judgment",
                        "evidence": ["answer", "reference"], "needs_review": False}
            raw = json.dumps({"choices": [{"message": {"content": json.dumps(decision)}}],
                              "usage": {"prompt_tokens": 30, "completion_tokens": 10}}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    judge_server = ThreadingHTTPServer(("127.0.0.1", 0), JudgeHandler)
    threading.Thread(target=judge_server.serve_forever, daemon=True).start()
    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend") + os.pathsep + str(ROOT),
           "AGENTEVAL_API_TOKEN": secrets.token_urlsafe(32),
           "AGENTEVAL_DATABASE_URL": os.environ.get("AGENTEVAL_QUEUE_DATABASE_URL") or "sqlite:///" + (directory / "queue.db").as_posix(),
           "AGENTEVAL_EXECUTION_MODE": "celery", "AGENTEVAL_BROKER_URL": broker,
           "AGENTEVAL_LEASE_SECONDS": "3", "AGENTEVAL_JUDGE_MAX_CALLS": "20",
           "AGENTEVAL_JUDGE_ENDPOINT": f"http://127.0.0.1:{judge_server.server_port}/chat/completions",
           "AGENTEVAL_JUDGE_MODEL": "protocol-fixture"}
    env["AGENTEVAL_JUDGE_API_KEY"] = ""
    subprocess.run([sys.executable, "-m", "agenteval", "init-db"], cwd=ROOT, env=env, check=True)
    processes = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (directory / "processes.log").open("w", encoding="utf-8") as log, httpx.Client(
            base_url=f"http://127.0.0.1:{port}/api/v1/", headers={"Authorization": "Bearer " + env["AGENTEVAL_API_TOKEN"]},
            trust_env=False, timeout=5) as client:
        def start(*args):
            process = subprocess.Popen([sys.executable, "-m", "agenteval", *args], cwd=ROOT,
                env=env, stdout=log, stderr=log, creationflags=flags)
            processes.append(process)
            return process

        def stop(process):
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)

        def wait(predicate, timeout=45):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    value = predicate()
                    if value:
                        return value
                except (httpx.RequestError, KeyError):
                    pass
                time.sleep(.15)
            raise AssertionError(f"Queue smoke timed out; inspect {directory / 'processes.log'}")

        def post(path, body):
            reply = client.post(path, json=body); reply.raise_for_status(); return reply.json()

        def get(path):
            reply = client.get(path); reply.raise_for_status(); return reply.json()

        try:
            api = start("serve", "--port", str(port))
            wait(lambda: client.get("capabilities").status_code == 200)
            project = post("projects", {"name": "Queue + LangGraph + Judge smoke"})
            agent = post("agents", {"project_id": project["id"], "name": "Actual graph", "adapter_type": "langgraph"})
            version = post(f"agents/{agent['id']}/versions", {"version": "1", "configuration": {"plugin_id": "example.order.graph"},
                "effects": "read_only", "capabilities": ["tool_calls", "workflow_steps", "retrieval_results"]})
            benchmark = post("benchmarks", {"project_id": project["id"], "name": "Orders"})
            cases = json.loads((ROOT / "benchmarks/orders.json").read_text(encoding="utf-8"))
            benchmark_version = post(f"benchmarks/{benchmark['id']}/versions", cases)
            profile = post("profile-versions", {"project_id": project["id"], "name": "Graph + judge", "version": "1",
                "evaluators": [{"instance_id": "answer", "id": "exact_match"},
                    {"instance_id": "steps", "id": "trajectory"},
                    {"instance_id": "judge", "id": "llm_judge", "config": {"rubric": "Compare answer and reference."}}]})
            body = {"agent_version_id": version["id"], "benchmark_version_id": benchmark_version["id"], "profile_version_id": profile["id"]}
            run = post("evaluations", body)
            assert get(f"evaluations/{run['id']}")["status"] == "queued"
            stop(api)
            api = start("serve", "--port", str(port))
            wait(lambda: client.get("capabilities").status_code == 200)
            assert get(f"evaluations/{run['id']}")["status"] == "queued"
            worker, dispatcher = start("worker"), start("dispatch")
            wait(lambda: get(f"evaluations/{run['id']}")["status"] == "completed")
            report = get(f"evaluations/{run['id']}/report")
            assert report["success_rate"] == 1 and report["attempts"] == 2
            assert len(get(f"evaluations/{run['id']}/judge-calls")["items"]) == 2
            # Actual worker crash during an invocation: no implicit second operation.
            slow = post("agents", {"project_id": project["id"], "name": "Slow fixture", "adapter_type": "python"})
            slow_version = post(f"agents/{slow['id']}/versions", {"version": "1", "configuration": {"plugin_id": "example.slow"}})
            slow_profile = post("profile-versions", {"project_id": project["id"], "name": "Crash", "version": "1",
                "evaluators": [{"instance_id": "answer", "id": "exact_match"}], "execution": {"timeout_seconds": 1.5}})
            crashing = post("evaluations", {**body, "agent_version_id": slow_version["id"], "profile_version_id": slow_profile["id"]})
            wait(lambda: any(i["status"] == "running" for i in get(f"evaluations/{crashing['id']}/work-items")["items"]))
            stop(worker)
            wait(lambda: any(i["status"] == "blocked" for i in get(f"evaluations/{crashing['id']}/work-items")["items"]))
            worker = start("worker")
            wait(lambda: get(f"evaluations/{crashing['id']}")["status"] == "completed")
            crash_report = get(f"evaluations/{crashing['id']}/report")
            assert crash_report["counts"]["unknown"] >= 1 and crash_report["attempts"] == 2
            output = {"broker": "real-redis" if redis_fixture is None else "fakeredis-protocol-fixture",
                      "api_restart_preserved_queue": True, "langgraph_success_rate": report["success_rate"],
                      "judge_protocol_calls": 2, "worker_crash_unknown": crash_report["counts"]["unknown"],
                      "silent_reexecution": False}
            print(json.dumps(output, indent=2))
            (directory / "result.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        finally:
            for process in processes:
                stop(process)
            judge_server.shutdown(); judge_server.server_close()
            if redis_fixture:
                redis_fixture.shutdown(); redis_fixture.server_close()


if __name__ == "__main__":
    main()
