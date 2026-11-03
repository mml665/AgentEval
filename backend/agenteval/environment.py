"""Deployment-owned state probes. Evidence is collected outside the Agent process."""
import sqlite3
import time
from pathlib import Path
from typing import Protocol

from pydantic import Field

from agenteval.contracts import Contract, canonical, digest, pointer


class StateCheck(Contract):
    path: str = Field(pattern=r"^/")
    expected: object


class StateAssertions(Contract):
    checks: list[StateCheck] = Field(min_length=1, max_length=100)


class EnvironmentAdapter(Protocol):
    def prepare(self, initial: dict) -> dict: ...
    def observe(self) -> dict: ...
    def reset(self) -> None: ...


class RefundEnvironment:
    """One private SQLite namespace per execution; never resets a shared business DB."""
    def __init__(self, root, execution_id):
        self.path = Path(root).resolve() / f"{execution_id}.sqlite3"

    def prepare(self, initial):
        if initial != {}:
            raise ValueError("sqlite.refund.v1 accepts an empty initial state only")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A redelivered execution must not erase a possibly committed external effect.
        if self.path.exists():
            raise FileExistsError("Environment namespace already exists")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE refunds (order_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        return {"adapter_id": "sqlite.refund.v1", "database_path": str(self.path)}

    def observe(self):
        with sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True) as db:
            return {"refunds": [{"order_id": row[0], "status": row[1]} for row in
                                db.execute("SELECT order_id, status FROM refunds ORDER BY order_id")]}

    def reset(self):
        # Reset data but keep the namespace marker, preventing unsafe reuse after a crash.
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM refunds")
        if self.observe() != {"refunds": []}:
            raise RuntimeError("Environment reset verification failed")


ENVIRONMENTS = {"sqlite.refund.v1": RefundEnvironment}


def collect(adapter, allow_reset):
    evidence = {"source": "independent_environment_adapter", "observed_at_ns": time.time_ns()}
    try:
        state = adapter.observe()
        evidence.update(status="observed", state=state, state_hash=digest(state))
    except Exception as exc:
        evidence.update(status="unavailable", error_code=type(exc).__name__)
    evidence["reset_status"] = "deferred_unknown_effect"
    if allow_reset:
        try:
            adapter.reset()
            evidence["reset_status"] = "verified"
        except Exception as exc:
            evidence.update(reset_status="failed", reset_error_code=type(exc).__name__)
    return evidence


def score_state(assertions, evidence):
    if not evidence or evidence.get("status") != "observed":
        return "missing_evidence", None, {"reason": "Independent environment snapshot unavailable"}
    checks = []
    for check in StateAssertions.model_validate(assertions).checks:
        try:
            actual = pointer(evidence["state"], check.path)
            passed = canonical(actual) == canonical(check.expected)
            checks.append({"path": check.path, "actual": actual, "expected": check.expected, "passed": passed})
        except (KeyError, IndexError, TypeError, ValueError):
            # A complete environment snapshot proves that the required field is absent.
            checks.append({"path": check.path, "expected": check.expected, "present": False, "passed": False})
    return "scored", float(all(c["passed"] for c in checks)), {
        "checks": checks, "state_hash": evidence["state_hash"], "source": evidence["source"]}
