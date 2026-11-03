"""Executable API contracts. Paths use RFC 6901 JSON Pointer."""
import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def pointer(value: Any, path: str) -> Any:
    if path == "":
        return value
    if not path.startswith("/"):
        raise ValueError("Path must be an RFC 6901 JSON Pointer")
    for token in path[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


class Named(Contract):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)


class AgentCreate(Named):
    project_id: str
    adapter_type: Literal["http", "python"]


class AgentVersionCreate(Contract):
    version: str = Field(min_length=1, max_length=64)
    configuration: dict[str, Any]
    capabilities: list[Literal["tool_calls", "workflow_steps", "retrieval_results", "final_state"]] = []


class Case(Contract):
    case_key: str = Field(min_length=1, max_length=128)
    input: dict[str, Any]
    assertions: dict[str, Any]
    tags: list[str] = []


class BenchmarkCreate(Named):
    project_id: str


class BenchmarkVersionCreate(Contract):
    version: str = Field(min_length=1, max_length=64)
    cases: list[Case] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_cases(self):
        keys = [c.case_key for c in self.cases]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate case_key")
        canonical(self.model_dump())
        return self


EvaluatorID = Literal["exact_match", "json_schema", "tool_selection", "tool_arguments", "trajectory", "rag"]


class EvaluatorSpec(Contract):
    instance_id: str = Field(min_length=1, max_length=64)
    id: EvaluatorID
    version: Literal["1.0"] = "1.0"
    required: bool = True
    hard_constraint: bool = False
    threshold: float = Field(default=1, ge=0, le=1)
    config: dict[str, Any] = {}

    @model_validator(mode="after")
    def check_config(self):
        from agenteval.evaluators import validate_config
        validate_config(self)
        if self.hard_constraint and not self.required:
            raise ValueError("Hard constraints must be required")
        return self


class ExecutionPolicy(Contract):
    repetitions: int = Field(default=1, ge=1, le=10)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_concurrency: int = Field(default=4, ge=1, le=16)
    max_retries: Literal[0] = 0


class Gate(Contract):
    min_decision_coverage: float = Field(default=1, ge=0, le=1)
    min_success_rate: float = Field(default=0.95, ge=0, le=1)
    max_success_rate_drop: float = Field(default=0, ge=0, le=1)
    core_case_keys: list[str] = []


class ProfileCreate(Named):
    project_id: str
    version: str = Field(min_length=1, max_length=64)
    evaluators: list[EvaluatorSpec] = Field(min_length=1, max_length=20)
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    gate: Gate = Field(default_factory=Gate)

    @model_validator(mode="after")
    def validate_profile(self):
        ids = [e.instance_id for e in self.evaluators]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate evaluator instance_id")
        if not any(e.required and not e.hard_constraint for e in self.evaluators):
            raise ValueError("A required task evaluator is necessary")
        return self


class RunCreate(Contract):
    agent_version_id: str
    benchmark_version_id: str
    profile_version_id: str


class CompareCreate(Contract):
    baseline_batch_id: str
    candidate_batch_id: str
    mode: Literal["strict", "exploratory"] = "strict"


def nearest_rank(values: list[float], quantile: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)] if values else None

