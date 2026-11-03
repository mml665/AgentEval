"""Two real local Agent implementations + independent-process HTTP RAG integration."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]


def main():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    os.environ.update(AGENTEVAL_HTTP_ALLOWLIST=f"127.0.0.1:{port}", AGENTEVAL_RETRIEVER_ENDPOINT=endpoint + "/retrieve",
                      AGENTEVAL_RERANKER_ENDPOINT=endpoint + "/rerank", PYTHONPATH=str(ROOT / "backend"))
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "examples.rag_service:app", "--host", "127.0.0.1", "--port", str(port)],
                               cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import httpx
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("RAG integration process exited")
            try:
                if httpx.get(endpoint + "/openapi.json", timeout=.2, trust_env=False).status_code == 200:
                    break
            except httpx.RequestError:
                time.sleep(.1)
        else:
            raise RuntimeError("RAG integration process did not become ready")
        from fastapi.testclient import TestClient
        from agenteval.api import create_app
        from agenteval.config import Settings
        from agenteval.db import Base
        from examples.v05_demo import create_demo
        with tempfile.TemporaryDirectory(prefix="agenteval-v05-") as folder:
            token = secrets.token_urlsafe(24)
            app = create_app(Settings(database_url=f"sqlite:///{Path(folder).as_posix()}/eval.db", api_token=token, execution_mode="local",
                                      environment_root=str(Path(folder) / "environments")))
            Base.metadata.create_all(app.state.engine)
            with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
                data = create_demo(client)
                reports = [client.get(f"/api/v1/evaluations/{r['id']}/report").json() for r in data["runs"]]
                assert [r["success_rate"] for r in reports] == [1, 0, 1], reports
                def post(path, body):
                    response = client.post("/api/v1/" + path, json=body)
                    response.raise_for_status()
                    return response.json()
                comparison = post("regressions/compare", {"baseline_batch_id": data["runs"][0]["grading_batch_id"], "candidate_batch_id": data["runs"][1]["grading_batch_id"]})
                assert comparison["report"]["gate"]["status"] == "failed"
                bm = post("benchmarks", {"project_id": data["project"]["id"], "name": "Independent HTTP RAG"})
                dataset = json.loads((ROOT / "benchmarks/rag_relevance.json").read_text(encoding="utf-8"))
                bv = post(f"benchmarks/{bm['id']}/versions", {"version": dataset["version"], "cases": dataset["cases"]})
                profile = post("profile-versions", {"project_id": data["project"]["id"], "name": "Recall/MRR/NDCG", "version": "1",
                    "evaluators": [{"instance_id": metric, "id": "rag", "threshold": 0, "config": {"metric": metric, "k": 2}} for metric in ("recall", "mrr", "ndcg")]})
                rag_reports = []
                for component in ("http.retriever", "http.reranker"):
                    run = post("component-evaluations", {"project_id": data["project"]["id"], "component_id": component,
                        "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]})
                    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
                    metrics = report["specialty_metrics"]["granularities"]["component"]["evaluators"]
                    assert all(m["coverage"] == 1 for m in metrics)
                    assert any(m["mean_score"] < 1 for m in metrics), "Counterexample must remain measurable"
                    rag_reports.append({"component": component, "metrics": metrics})
                result = {"state_success_rates": [r["success_rate"] for r in reports], "regression_gate": comparison["report"]["gate"],
                          "rag": rag_reports, "scope": "Local integration fixtures and developer-authored labels; no production Agent or real LLM calibration"}
                output = ROOT / ".build/v05-smoke.json"
                output.parent.mkdir(exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print("v0.5 smoke passed: state [100%, 0%, 100%], regression failed as expected, independent HTTP RAG measured")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


if __name__ == "__main__":
    main()
