from typing import Literal
from pydantic import Field, model_validator
from agenteval.contracts import Contract, AgentVersionCreate, BenchmarkVersionCreate, ExecutionPolicy, Gate


class ConnectionCheck(Contract):
    adapter_type: Literal["http", "python", "langgraph"]
    configuration: dict


class GuidedRun(Contract):
    request_key: str = Field(min_length=16, max_length=128)
    project_id: str | None = None
    project_name: str = Field(default="我的质量项目", min_length=1, max_length=120)
    agent_name: str = Field(min_length=1, max_length=120)
    adapter_type: Literal["http", "python", "langgraph"]
    agent: AgentVersionCreate
    benchmark: BenchmarkVersionCreate
    goals: list[Literal["answer", "tools", "trajectory", "retrieval", "state"]] = Field(min_length=1)
    environment_id: str | None = None
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    gate: Gate = Field(default_factory=Gate)

    @model_validator(mode="after")
    def unique_goals(self):
        if len(set(self.goals)) != len(self.goals):
            raise ValueError("Duplicate evaluation goal")
        return self


class ImportIssues(Contract):
    grading_batch_id: str


class IssueUpdate(Contract):
    expected_revision: int = Field(ge=0)
    status: Literal["open", "confirmed", "fix_in_progress", "ready_for_retest"]
    owner: str = Field(default="", max_length=120)
    confirmed_cause: str = Field(default="", max_length=4000)
    note: str = Field(min_length=1, max_length=4000)


class IssueRetest(Contract):
    expected_revision: int = Field(ge=0)
    grading_batch_id: str
    note: str = Field(min_length=1, max_length=4000)


class CreateRelease(Contract):
    comparison_id: str
    note: str = Field(default="", max_length=4000)


class StartRetest(Contract):
    agent_version_id: str
    request_key: str = Field(min_length=16, max_length=128)
