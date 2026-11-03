import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv(
        "AGENTEVAL_DATABASE_URL", "sqlite:///./data/agenteval.db"))
    api_token: str = field(default_factory=lambda: os.getenv("AGENTEVAL_API_TOKEN", ""))
    http_allowlist: tuple[str, ...] = field(default_factory=lambda: tuple(
        x.strip().lower() for x in os.getenv(
            "AGENTEVAL_HTTP_ALLOWLIST", "127.0.0.1:9001,localhost:9001").split(",") if x.strip()))

