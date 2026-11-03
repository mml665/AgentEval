import os
import math
from urllib.parse import urlsplit
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv(
        "AGENTEVAL_DATABASE_URL", "sqlite:///./data/agenteval.db"))
    api_token: str = field(default_factory=lambda: os.getenv("AGENTEVAL_API_TOKEN", ""))
    admin_token: str = field(default_factory=lambda: os.getenv("AGENTEVAL_ADMIN_TOKEN", ""), repr=False)
    execution_mode: str = field(default_factory=lambda: os.getenv("AGENTEVAL_EXECUTION_MODE", "local"))
    broker_url: str = field(default_factory=lambda: os.getenv("AGENTEVAL_BROKER_URL", "redis://127.0.0.1:6379/0"))
    lease_seconds: float = field(default_factory=lambda: float(os.getenv("AGENTEVAL_LEASE_SECONDS", "30")))
    project_concurrency: int = field(default_factory=lambda: int(os.getenv("AGENTEVAL_PROJECT_CONCURRENCY", "8")))
    judge_endpoint: str = field(default_factory=lambda: os.getenv("AGENTEVAL_JUDGE_ENDPOINT", ""))
    judge_model: str = field(default_factory=lambda: os.getenv("AGENTEVAL_JUDGE_MODEL", ""))
    judge_api_key: str = field(default_factory=lambda: os.getenv("AGENTEVAL_JUDGE_API_KEY", ""), repr=False)
    judge_max_calls: int = field(default_factory=lambda: int(os.getenv("AGENTEVAL_JUDGE_MAX_CALLS", "100")))
    environment_root: str = field(default_factory=lambda: os.getenv("AGENTEVAL_ENVIRONMENT_ROOT", "./data/environments"))

    def __post_init__(self):
        if self.execution_mode not in ("local", "celery") or not math.isfinite(self.lease_seconds) or self.lease_seconds < 1 or self.project_concurrency < 1 or self.judge_max_calls < 1:
            raise ValueError("Invalid execution mode, lease or concurrency configuration")
        if self.judge_endpoint:
            endpoint = urlsplit(self.judge_endpoint)
            if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("Judge endpoint must be HTTP(S), without inline credentials, query or fragment")
    http_allowlist: tuple[str, ...] = field(default_factory=lambda: tuple(
        x.strip().lower() for x in os.getenv(
            "AGENTEVAL_HTTP_ALLOWLIST", "127.0.0.1:9001,localhost:9001").split(",") if x.strip()))

