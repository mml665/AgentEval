import os
import math
from urllib.parse import urlsplit
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv(
        "AGENTEVAL_DATABASE_URL", "sqlite:///./data/agenteval.db"), repr=False)
    api_token: str = field(default_factory=lambda: os.getenv("AGENTEVAL_API_TOKEN", ""), repr=False)
    auth_mode: str = field(default_factory=lambda: os.getenv("AGENTEVAL_AUTH_MODE", "legacy"))
    legacy_token_enabled: bool = field(default_factory=lambda: os.getenv("AGENTEVAL_LEGACY_TOKEN_ENABLED", "0") == "1")
    session_cookie_secure: bool = field(default_factory=lambda: os.getenv("AGENTEVAL_SESSION_COOKIE_SECURE", "1") == "1")
    session_origins: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip() for x in os.getenv("AGENTEVAL_SESSION_ORIGINS", "https://localhost").split(",") if x.strip()))
    session_idle_seconds: int = field(default_factory=lambda: int(os.getenv("AGENTEVAL_SESSION_IDLE_SECONDS", "3600")))
    session_absolute_seconds: int = field(default_factory=lambda: int(os.getenv("AGENTEVAL_SESSION_ABSOLUTE_SECONDS", "43200")))
    credential_fingerprint_key: str = field(default_factory=lambda: os.getenv("AGENTEVAL_CREDENTIAL_FINGERPRINT_KEY", ""), repr=False)
    plugin_env_allowlist: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip() for x in os.getenv("AGENTEVAL_PLUGIN_ENV_ALLOWLIST", "").split(",") if x.strip()))
    trusted_proxy_ips: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip() for x in os.getenv("AGENTEVAL_TRUSTED_PROXY_IPS", "").split(",") if x.strip()))
    admin_token: str = field(default_factory=lambda: os.getenv("AGENTEVAL_ADMIN_TOKEN", ""), repr=False)
    execution_mode: str = field(default_factory=lambda: os.getenv("AGENTEVAL_EXECUTION_MODE", "local"))
    broker_url: str = field(default_factory=lambda: os.getenv("AGENTEVAL_BROKER_URL", "redis://127.0.0.1:6379/0"), repr=False)
    lease_seconds: float = field(default_factory=lambda: float(os.getenv("AGENTEVAL_LEASE_SECONDS", "30")))
    project_concurrency: int = field(default_factory=lambda: int(os.getenv("AGENTEVAL_PROJECT_CONCURRENCY", "8")))
    judge_endpoint: str = field(default_factory=lambda: os.getenv("AGENTEVAL_JUDGE_ENDPOINT", ""))
    judge_model: str = field(default_factory=lambda: os.getenv("AGENTEVAL_JUDGE_MODEL", ""))
    judge_api_key: str = field(default_factory=lambda: os.getenv("AGENTEVAL_JUDGE_API_KEY", ""), repr=False)
    judge_max_calls: int = field(default_factory=lambda: int(os.getenv("AGENTEVAL_JUDGE_MAX_CALLS", "100")))
    environment_root: str = field(default_factory=lambda: os.getenv("AGENTEVAL_ENVIRONMENT_ROOT", "./data/environments"))

    def __post_init__(self):
        if self.auth_mode not in ("legacy", "team") or not 60 <= self.session_idle_seconds <= self.session_absolute_seconds <= 604800:
            raise ValueError("Invalid authentication mode or session lifetimes")
        import ipaddress
        for value in self.trusted_proxy_ips: ipaddress.ip_network(value, strict=False)
        for origin in self.session_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("Session origins must be exact HTTP(S) origins")
            if not self.session_cookie_secure and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Insecure session cookies are limited to explicitly configured loopback origins")
            if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Personal login requires HTTPS outside loopback development")
        if self.execution_mode not in ("local", "celery") or not math.isfinite(self.lease_seconds) or self.lease_seconds < 1 or self.project_concurrency < 1 or self.judge_max_calls < 1:
            raise ValueError("Invalid execution mode, lease or concurrency configuration")
        if self.judge_endpoint:
            endpoint = urlsplit(self.judge_endpoint)
            if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("Judge endpoint must be HTTP(S), without inline credentials, query or fragment")
    http_allowlist: tuple[str, ...] = field(default_factory=lambda: tuple(
        x.strip().lower() for x in os.getenv(
            "AGENTEVAL_HTTP_ALLOWLIST", "127.0.0.1:9001,localhost:9001").split(",") if x.strip()))

