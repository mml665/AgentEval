import itertools
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from agenteval.tool_scoring import score_calls
from agenteval.environment import collect


@pytest.mark.parametrize("order", list(itertools.permutations(["ok", "missing", "bad"])))
def test_mixed_tool_evidence_is_order_invariant(order):
    calls = {"ok": {"call_id": "ok", "name": "get", "arguments": {"id": 1}, "output": 1, "status": "ok"},
             "missing": {"call_id": "missing", "name": "get", "arguments": {"id": 1}, "status": "ok"},
             "bad": {"call_id": "bad", "name": "get", "arguments": {"id": 2}, "status": "ok"}}
    rules = [{"call_id": key, "name": "get", "expected_arguments": {"id": 1}} for key in order]
    status, score, details = score_calls(rules, [calls[k] for k in order], {}, complete=True)
    assert status == "scored" and score == 0
    assert details["counts"] == {"passed": 1, "failed": 1, "unknown": 1}
    assert {r["call_id"]: r["passed"] for r in details["calls"]} == {"ok": True, "missing": None, "bad": False}


def test_unknown_does_not_contaminate_later_success():
    rules = [{"call_id": key, "name": "get"} for key in ("a", "b")]
    calls = [{"call_id": "a", "name": "get", "arguments": {}, "status": "ok"},
             {"call_id": "b", "name": "get", "arguments": {}, "output": None, "status": "ok"}]
    status, score, evidence = score_calls(rules, calls, {}, complete=True)
    assert status == "missing_evidence" and score is None
    assert evidence["calls"][1]["passed"] is True


def test_failed_snapshot_commit_must_not_reset():
    class Adapter:
        def observe(self): return {"state": "done"}
        def reset(self): raise AssertionError("Must never reset without durable evidence")
    def failed_commit(value): raise ConnectionError("Database unavailable")
    with pytest.raises(ConnectionError):
        collect(Adapter(), True, failed_commit)
    assert collect(Adapter(), True)["reset_status"] == "deferred_uncommitted_snapshot"


@pytest.mark.parametrize("crash_after_reset", [False, True])
def test_real_process_crash_after_checkpoint_keeps_terminal_snapshot(tmp_path, crash_after_reset):
    script = r'''
import os,sqlite3,sys,threading
from pathlib import Path
sys.path[:0]=['backend','.']
from fastapi.testclient import TestClient
from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.db import Base
from agenteval.environment import RefundEnvironment
from examples.v05_demo import create_demo
root=Path(sys.argv[1])
original_reset = RefundEnvironment.reset
crash_lock = threading.Lock()
def crash(self):
 with crash_lock:
  if sys.argv[2] == 'after': original_reset(self)
  (root/'crashed_namespace.txt').write_text(self.path.name)
  os._exit(77)
RefundEnvironment.reset=crash
app=create_app(Settings(database_url='sqlite:///'+(root/'eval.db').as_posix(),api_token='test-token-for-crash-only',environment_root=str(root/'env')))
Base.metadata.create_all(app.state.engine)
with TestClient(app,headers={'Authorization':'Bearer test-token-for-crash-only'}) as client: create_demo(client)
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), "after" if crash_after_reset else "before"], timeout=30)
    assert result.returncode == 77
    crashed_namespace = (tmp_path / "crashed_namespace.txt").read_text()
    with sqlite3.connect(tmp_path / "eval.db") as db:
        row = db.execute("SELECT environment_evidence,response,evidence_provenance FROM case_executions WHERE id=?", (Path(crashed_namespace).stem,)).fetchone()
        state, response, provenance = map(json.loads, row)
        assert state["reset_status"] == "pending" and state["execution_outcome"] == "completed"
        assert state["state"]["refunds"] and response["output"]["status"] == "requested"
        assert provenance["environment"]["independent"] is True
        db.execute("UPDATE work_items SET lease_until=0 WHERE status='running'"); db.commit()
    if crash_after_reset:
        with sqlite3.connect(tmp_path / "env" / crashed_namespace) as fixture:
            assert fixture.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 0
    from agenteval.config import Settings
    from agenteval.db import make_database, Execution
    from agenteval.durable import Coordinator
    from sqlalchemy import select
    engine, sessions = make_database('sqlite:///' + (tmp_path / 'eval.db').as_posix())
    Coordinator(sessions, Settings()).recover()
    with sessions() as session:
        execution = session.get(Execution, Path(crashed_namespace).stem)
        assert execution.environment_evidence["state"] == state["state"]
        assert execution.status == "outcome_unknown"
    engine.dispose()


@pytest.mark.parametrize("mode", ["mock", "error", "timeout"])
def test_fault_injection_at_controlled_tool_boundary(client, setup_run, mode):
    s = setup_run
    fault = {"tool_name": "get_order", "mode": mode, "mock_output": {"order_id": "1001", "status": "wrong"}}
    profile = s["post"]("profile-versions", {"project_id": s["project"]["id"], "name": mode, "version": "1",
        "evaluators": [{"instance_id": "answer", "id": "exact_match"}], "execution": {"tool_faults": [fault]}})
    run = s["post"]("evaluations", {**s["body"], "profile_version_id": profile["id"]})
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["counts"]["failed"] == 2
    execution = client.get(f"/api/v1/executions/{report['trials'][0]['execution_id']}").json()
    assert execution["evidence_provenance"]["output"]["independent"] is False
    assert execution["evidence_provenance"]["fault_injection"]["source"] == "platform_profile"
    calls = execution["response"]["observations"]["tool_calls"]
    assert calls[0]["fault_injection"]["mode"] == mode
    assert report["trial_scoring_coverage"] == report["attempt_scoring_coverage"]


def test_trial_and_attempt_scoring_denominators_are_distinct(client, setup_run):
    from sqlalchemy import select
    from agenteval.db import Execution, TrialAssessment
    s = setup_run
    run = s["post"]("evaluations", s["body"])
    with client.app.state.sessions() as session:
        assessment = session.scalar(select(TrialAssessment).filter_by(batch_id=run["grading_batch_id"]))
        retry = Execution(trial_id=assessment.trial_id, attempt_index=1, status="completed", response={"output": {}})
        session.add(retry); session.flush()
        assessment.selected_execution_id = retry.id
        session.commit()
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["attempt_scoring_coverage"] == pytest.approx(2 / 3)
    assert report["trial_scoring_coverage"] == .5
    assert report["planned_trials"] == 2 and report["attempts"] == 3
