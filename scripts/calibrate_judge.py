"""Reproducible calibration through the normal Runner/Judge, or imported offline ratings."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from agenteval.calibration import analyze
from agenteval.config import Settings
from agenteval.contracts import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks/judge_calibration.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, choices=range(2, 11), default=3)
    parser.add_argument("--predictions", type=Path, help="Offline JSON array; does not call any model")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    labels = [{k: s[k] for k in ("case_key", "label", "rationale")} for s in dataset["samples"]]
    if args.predictions:
        value = analyze(labels, json.loads(args.predictions.read_text(encoding="utf-8")), planned_repetitions=args.repetitions)
        value.update(execution_source="imported_predictions", annotation_source=dataset["annotation_source"])
    else:
        settings = Settings()
        if not settings.judge_endpoint or not settings.judge_model:
            parser.error("Configure AGENTEVAL_JUDGE_ENDPOINT / AGENTEVAL_JUDGE_MODEL (and provider key) to run real calibration; no fixture fallback")
        from fastapi.testclient import TestClient
        from agenteval.api import create_app
        from agenteval.db import Base
        path = ROOT / ".build" / f"calibration-{uuid4().hex}.db"
        token = secrets.token_urlsafe(24)
        app = create_app(replace(settings, database_url=f"sqlite:///{path.as_posix()}", api_token=token, execution_mode="local"))
        Base.metadata.create_all(app.state.engine)
        with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
            def post(path, body):
                reply = client.post("/api/v1/" + path, json=body)
                reply.raise_for_status()
                return reply.json()
            p = post("projects", {"name": "Judge calibration"})
            agent = post("agents", {"project_id": p["id"], "name": "Calibration subjects", "adapter_type": "python"})
            av = post(f"agents/{agent['id']}/versions", {"version": "1", "configuration": {"plugin_id": "calibration.echo"}, "effects": "read_only"})
            benchmark = post("benchmarks", {"project_id": p["id"], "name": dataset["version"]})
            bv = post(f"benchmarks/{benchmark['id']}/versions", {"version": dataset["version"], "cases": [
                {"case_key": s["case_key"], "input": s["input"], "assertions": {"expected_output": s["reference"]}} for s in dataset["samples"]]})
            profile = post("profile-versions", {"project_id": p["id"], "name": "Calibration", "version": "1",
                "evaluators": [{"instance_id": "judge", "id": "llm_judge", "threshold": .8, "config": {"rubric": dataset["rubric"]}}],
                "execution": {"repetitions": args.repetitions}})
            run = post("evaluations", {"agent_version_id": av["id"], "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]})
            value = post(f"evaluations/{run['id']}/calibration", {"grading_batch_id": run["grading_batch_id"], "instance_id": "judge",
                "annotation_source": dataset["annotation_source"], "labels": labels})
            value.update(execution_source="configured_judge_endpoint", local_database=str(path))
    value["dataset_hash"] = digest(dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Calibration report: {args.output}")


if __name__ == "__main__":
    main()
