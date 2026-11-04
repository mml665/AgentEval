from typing import Literal
from pydantic import Field, field_validator
from agenteval.contracts import Contract


class Login(Contract):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=256, repr=False)

    @field_validator("username")
    @classmethod
    def normalize(cls, value): return value.lower()


class NewUser(Login):
    password: str = Field(min_length=12, max_length=256, repr=False)
    display_name: str = Field(min_length=1, max_length=120)
    is_admin: bool = False


class UserState(Contract):
    active: bool


class PasswordChange(Contract):
    password: str = Field(min_length=12, max_length=256, repr=False)


class Membership(Contract):
    user_id: str
    role: Literal["evaluator", "viewer", "remove"]


class NewMachine(Contract):
    name: str = Field(min_length=1, max_length=120)
    project_ids: list[str] = Field(min_length=1, max_length=100)
    permission: Literal["read", "evaluate"]
    expires_in_seconds: int = Field(default=86400, ge=300, le=2592000)


class NewCredential(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=120)
    env_name: str = Field(pattern=r"^AGENTEVAL_AGENT_SECRET_[A-Z0-9_]+$", max_length=160)
    auth_mode: Literal["bearer", "header"]
    header_name: str = Field(default="Authorization", min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9-]*$")


class TestHttp(Contract):
    agent_version_id: str
    benchmark_version_id: str
    case_id: str
    confirm_test_environment: Literal[True]
    timeout_seconds: float = Field(default=10, ge=.1, le=30)
