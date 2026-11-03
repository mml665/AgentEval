"""End-to-end smoke check through real API/HTTP processes, with isolated data."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    build = ROOT / ".build"
    build.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="smoke-", dir=build))
    api_port, http_port = free_port(), free_port()
    while api_port == http_port:
        http_port = free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend") + os.pathsep + str(ROOT),
           "AGENTEVAL_API_TOKEN": secrets.token_urlsafe(32),
           "AGENTEVAL_DATABASE_URL": "sqlite:///" + (work / "demo.db").as_posix(),
           "AGENTEVAL_HTTP_ALLOWLIST": f"127.0.0.1:{http_port}"}
    subprocess.run([sys.executable, "-m", "agenteval", "init-db"], cwd=ROOT, env=env, check=True, timeout=30)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    processes = []
    with (work / "servers.log").open("w", encoding="utf-8") as log:
        try:
            for application, port in (("agenteval.api:app", api_port), ("examples.http_agent:app", http_port)):
                processes.append(subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", application, "--host", "127.0.0.1", "--port", str(port)],
                    cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=flags))
            with httpx.Client(trust_env=False) as client:
                deadline = time.monotonic() + 20
                ready = False
                while time.monotonic() < deadline:
                    try:
                        api = client.get(f"http://127.0.0.1:{api_port}/health", timeout=1)
                        fixture = client.get(f"http://127.0.0.1:{http_port}/openapi.json", timeout=1)
                        if api.status_code == fixture.status_code == 200:
                            ready = True
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.1)
                if not ready:
                    raise RuntimeError(f"Smoke servers did not start; inspect {work / 'servers.log'}")
            result = subprocess.run([sys.executable, "-m", "examples.demo", "--url", f"http://127.0.0.1:{api_port}",
                "--with-http", "--http-endpoint", f"http://127.0.0.1:{http_port}/invoke"],
                cwd=ROOT, env=env, check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
            report = json.loads(result.stdout)
            (work / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
            print(f"Smoke evidence: {work.relative_to(ROOT).as_posix()}")
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()

