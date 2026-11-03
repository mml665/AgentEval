"""Create actual persisted runs, compare two versions, optionally exercise HTTP."""
import argparse
import json
import os
from pathlib import Path

from sdk import AgentEvalClient

CAPABILITIES = ["tool_calls", "workflow_steps", "retrieval_results"]
EVALUATORS = [
    {"instance_id": "answer", "id": "exact_match"},
    {"instance_id": "tools", "id": "tool_selection", "hard_constraint": True,
     "config": {"forbidden": ["delete_order"]}},
    {"instance_id": "arguments", "id": "tool_arguments"},
    {"instance_id": "steps", "id": "trajectory"},
    {"instance_id": "retrieval", "id": "rag", "config": {"metric": "recall", "k": 5}},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--with-http", action="store_true")
    parser.add_argument("--http-endpoint", default="http://127.0.0.1:9001/invoke")
    args = parser.parse_args()
    token = os.environ.get("AGENTEVAL_API_TOKEN")
    if not token:
        parser.error("Set AGENTEVAL_API_TOKEN before running the demo")
    with AgentEvalClient(args.url, token) as client:
        project = client.post("projects", {"name": "Order quality demonstration"})
        project_id = project["id"]
        agent = client.post("agents", {"project_id": project_id, "name": "Order Agent", "adapter_type": "python"})
        versions = [client.post(f"agents/{agent['id']}/versions", {
            "version": f"{n}.0", "configuration": {"plugin_id": f"example.order.v{n}"},
            "capabilities": CAPABILITIES}) for n in (1, 2)]
        benchmark = client.post("benchmarks", {"project_id": project_id, "name": "Orders"})
        data = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/orders.json").read_text(encoding="utf-8"))
        benchmark_version = client.post(f"benchmarks/{benchmark['id']}/versions", data)
        profile = client.post("profile-versions", {"project_id": project_id, "name": "Order rules", "version": "1.0",
                               "evaluators": EVALUATORS, "gate": {"core_case_keys": ["order-1001"]}})
        batches = []
        for version in versions:
            run = client.post("evaluations", {"agent_version_id": version["id"],
                              "benchmark_version_id": benchmark_version["id"], "profile_version_id": profile["id"]})
            settled = client.wait(run["id"])
            if settled["status"] != "completed":
                raise RuntimeError(f"Demo run did not complete: {settled['status']}")
            batches.append(run["grading_batch_id"])
        comparison = client.post("regressions/compare", {"baseline_batch_id": batches[0], "candidate_batch_id": batches[1]})
        report = comparison["report"]
        if report["new_failures"] != ["order-1001"] or report["gate"]["status"] != "failed":
            raise AssertionError("Regression demo did not identify the expected failure")
        output = {"comparison_id": comparison["id"], "baseline_success": report["baseline"]["success_rate"],
                  "candidate_success": report["candidate"]["success_rate"], "new_failures": report["new_failures"],
                  "gate": report["gate"]}
        if args.with_http:
            http_agent = client.post("agents", {"project_id": project_id, "name": "HTTP Orders", "adapter_type": "http"})
            http_version = client.post(f"agents/{http_agent['id']}/versions", {
                "version": "1.0", "configuration": {"endpoint": args.http_endpoint}, "capabilities": CAPABILITIES})
            run = client.post("evaluations", {"agent_version_id": http_version["id"],
                              "benchmark_version_id": benchmark_version["id"], "profile_version_id": profile["id"]})
            client.wait(run["id"])
            output["http_success"] = client.get(f"evaluations/{run['id']}/report")["success_rate"]
            if output["http_success"] != 1:
                raise AssertionError("HTTP example failed")
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

