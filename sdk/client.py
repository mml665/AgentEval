import time

import httpx


class AgentEvalClient:
    """Small synchronous client; reads its token from the caller, never logs it."""
    def __init__(self, base_url: str, token: str):
        self.http = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1/",
                                 headers={"Authorization": f"Bearer {token}"},
                                 timeout=30, trust_env=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def get(self, path, **params):
        response = self.http.get(path.lstrip("/"), params=params)
        response.raise_for_status()
        return response.json()

    def post(self, path, body=None, **headers):
        response = self.http.post(path.lstrip("/"), json=body, headers=headers)
        response.raise_for_status()
        return response.json()

    def list_evaluations(self, project_id, **filters):
        """Server-side q/status/quality/version/time/sort filters plus offset/limit."""
        return self.get("evaluations", project_id=project_id, **filters)

    def wait(self, run_id, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.get(f"evaluations/{run_id}")
            if run["status"] in ("completed", "failed", "cancelled"):
                return run
            time.sleep(.1)
        raise TimeoutError("Evaluation did not settle before client deadline")

