from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, model_validator
from agenteval.contracts import Contract, RunCreate, ExecutionPolicy


class ReuseRun(RunCreate):
    project_id: str
    request_key: str = Field(min_length=16, max_length=128)


class CaseDocument(Contract):
    document: str = Field(max_length=1_900_000)
    version: str = Field(default="preview", min_length=1, max_length=64)
    agent_version_id: str | None = None
    profile_version_id: str | None = None


class SetBaseline(Contract):
    agent_id: str
    benchmark_version_id: str
    grading_batch_id: str
    expected_revision: int = Field(default=0, ge=0)


class DraftState(Contract):
    mode: Literal["new", "reuse"] = "new"
    step: int = Field(default=0, ge=0, le=2)
    request_key: str = Field(min_length=16, max_length=128)
    adapter: Literal["http", "python", "langgraph"] = "python"
    plugin: str = Field(default="example.order.v1", max_length=200)
    endpoint: str = Field(default="", max_length=2048)
    credential_version_id: str | None = None
    name: str = Field(default="我的 Agent", max_length=120)
    version: str = Field(default="1.0", min_length=1, max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=4)
    goals: list[str] = Field(default_factory=list, max_length=5)
    environment: str = Field(default="", max_length=120)
    cases: str = Field(default="[]", max_length=1_900_000)
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    pending_submission: bool = False
    agent_version_id: str | None = None
    benchmark_version_id: str | None = None
    profile_version_id: str | None = None

    @model_validator(mode="after")
    def no_inline_credentials(self):
        p = urlsplit(self.endpoint)
        if p.username or p.password or p.query or p.fragment: raise ValueError("Endpoint must not contain credentials, query or fragment")
        return self


class SaveDraft(Contract):
    expected_revision: int = Field(default=0, ge=0)
    payload: DraftState
