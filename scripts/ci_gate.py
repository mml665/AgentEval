"""Submit a pinned evaluation and return the strict release gate as JSON."""
import argparse
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx

from sdk.client import AgentEvalClient


EXIT = {"ready": 0, "blocked": 1, "insufficient": 2, "error": 3}


def run_gate(client, payload, baseline_batch_id, timeout):
    deadline = time.monotonic() + timeout
    result = {"project_id": payload["project_id"], "baseline_batch_id": baseline_batch_id}
    try:
        run = client.post("onboarding/reuse", payload)
        result["candidate_run_id"] = run["id"]
        while time.monotonic() < deadline:
            run = client.get(f"evaluations/{run['id']}")
            if run["status"] in ("completed", "failed", "cancelled"):
                initial = next((batch for batch in run["grading_batches"] if batch["revision"] == 0), None)
                if run["status"] != "completed" or not initial or initial["status"] != "completed":
                    return {**result, "status": "insufficient", "reasons": ["CANDIDATE_NOT_COMPLETED"]}
                result["candidate_batch_id"] = initial["id"]
                return client.get("quality/ci-gate", project_id=payload["project_id"],
                                  baseline_batch_id=baseline_batch_id, candidate_batch_id=initial["id"])
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 409 and "candidate_batch_id" in result:
            return {**result, "status": "insufficient", "reasons": ["RESULTS_NOT_COMPARABLE"]}
        return {**result, "status": "error", "reasons": [f"HTTP_{status}"]}
    except (httpx.RequestError, ValueError, KeyError, TypeError):
        return {**result, "status": "error", "reasons": ["SERVICE_REQUEST_FAILED"]}
    return {**result, "status": "insufficient", "reasons": ["EVALUATION_DEADLINE_EXCEEDED"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("AGENTEVAL_URL", ""))
    for name in ("project-id", "agent-version-id", "benchmark-version-id", "profile-version-id", "baseline-batch-id", "request-key"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--report", type=Path, default=Path("agenteval-gate.json"))
    args = parser.parse_args()
    token = os.getenv("AGENTEVAL_CI_TOKEN", "")
    try:
        url = urlsplit(args.url)
        valid_url = bool(url.hostname and not (url.username or url.password or url.query or url.fragment)
                         and (url.scheme == "https" or url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")))
    except ValueError:
        valid_url = False
    if valid_url and token and 16 <= len(args.request_key) <= 128 and 1 <= args.timeout <= 3600:
        payload = {key: getattr(args, key) for key in ("project_id", "agent_version_id", "benchmark_version_id",
                                                       "profile_version_id", "request_key")}
        with AgentEvalClient(args.url, token) as client:
            result = run_gate(client, payload, args.baseline_batch_id, args.timeout)
    else:
        result = {"status": "error", "reasons": ["INVALID_URL_TOKEN_REQUEST_KEY_OR_TIMEOUT"]}
    status = result.get("status")
    if status not in EXIT:
        status = "error"
        result = {"status": status, "reasons": ["INVALID_GATE_RESPONSE"]}
    report = json.dumps(result, ensure_ascii=True, indent=2)
    args.report.write_text(report + "\n", encoding="utf-8")
    print(report)
    if output := os.getenv("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"status={status}\n")
    return EXIT[status]


if __name__ == "__main__":
    raise SystemExit(main())
