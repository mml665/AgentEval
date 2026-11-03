import os
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

    def __post_init__(self):
        if self.execution_mode not in ("local", "celery") or self.lease_seconds < 1 or self.project_concurrency < 1:
            raise ValueError("Invalid execution mode, lease or concurrency configuration")
    http_allowlist: tuple[str, ...] = field(default_factory=lambda: tuple(
        x.strip().lower() for x in os.getenv(
            "AGENTEVAL_HTTP_ALLOWLIST", "127.0.0.1:9001,localhost:9001").split(",") if x.strip()))

