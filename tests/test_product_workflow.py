from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from agenteval.db import Agent, Project, QualityIssue, ReleaseDecision


@pytest.fixture(autouse=True)
def isolated_environments(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTEVAL_ENVIRONMENT_ROOT", str(tmp_path / "environments"))


def guided_body(project_id=None):
    return {"request_key": str(uuid4()), "project_id": project_id, "project_name": "Guided project", "agent_name": "Orders",
        "adapter_type": "python", "agent": {"version": "1", "configuration": {"plugin_id": "example.order.v1"}, "capabilities": []},
        "goals": ["answer"], "benchmark": {"version": "1", "cases": [{"case_key": "one", "input": {"order_id": "1001"},
            "assertions": {"expected_output": {"order_id": "1001", "status": "shipped"}}}]}}


def test_guided_run_uses_normal_engine_and_receipt_is_atomic(client):
    body = guided_body()
    response = client.post("/api/v1/onboarding/evaluations", json=body)
    assert response.status_code == 202, response.text
    run = response.json()
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert report["success_rate"] == 1
    duplicate = client.post("/api/v1/onboarding/evaluations", json=body)
    assert duplicate.status_code == 202 and duplicate.json()["id"] == run["id"]
    body["agent_name"] = "Changed request"
    assert client.post("/api/v1/onboarding/evaluations", json=body).status_code == 409
    with client.app.state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 1
        assert session.scalar(select(func.count()).select_from(Agent)) == 1


def test_guided_preflight_rolls_back_all_resources(client):
    body = guided_body()
    body["goals"] = ["tools"]
    response = client.post("/api/v1/onboarding/evaluations", json=body)
    assert response.status_code == 422
    with client.app.state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 0
    body = guided_body()
    body["environment_id"] = "sqlite.refund.v1"
    body["agent"]["effects"] = "read_only"
    assert client.post("/api/v1/onboarding/evaluations", json=body).status_code == 422
    with client.app.state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 0


def test_connection_check_does_not_claim_observed_capabilities(client):
    value = client.post("/api/v1/onboarding/check", json={"adapter_type": "python", "configuration": {"plugin_id": "example.order.v1"}}).json()
    assert value["status"] == "registered" and value["capabilities_verified"] is False
    assert client.post("/api/v1/onboarding/check", json={"adapter_type": "http", "configuration": {"endpoint": "http://unlisted.invalid/invoke"}}).status_code == 422


def create_failed_case(client, setup_run):
    s = setup_run
    bad = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "bad", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": s["version"]["capabilities"]})
    run = s["post"]("evaluations", {**s["body"], "agent_version_id": bad["id"]})
    imported = s["post"]("quality/issues/import", {"grading_batch_id": run["grading_batch_id"]})
    assert len(imported["items"]) == 1
    return s, run, imported["items"][0]


def move_to_retest(s, issue):
    for status in ("confirmed", "fix_in_progress", "ready_for_retest"):
        issue = s["post"](f"quality/issues/{issue['id']}/update", {"expected_revision": issue["revision"], "status": status,
            "owner": "Developer", "confirmed_cause": "Verified output status mapping", "note": status})
    return issue


def test_issue_lifecycle_requires_compatible_passing_retest_and_keeps_history(client, setup_run):
    s, run, issue = create_failed_case(client, setup_run)
    duplicate = s["post"]("quality/issues/import", {"grading_batch_id": run["grading_batch_id"]})
    assert duplicate["items"][0]["id"] == issue["id"]
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/update", json={"expected_revision": 0, "status": "ready_for_retest", "note": "skip"}).status_code == 409
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/update", json={"expected_revision": 0, "status": "resolved", "note": "manual close"}).status_code == 422
    issue = move_to_retest(s, issue)
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/update", json={"expected_revision": 0, "status": "ready_for_retest", "note": "stale"}).status_code == 409
    fixed = s["post"](f"quality/issues/{issue['id']}/evaluations", {"agent_version_id": s["version"]["id"], "request_key": str(uuid4())})
    fixed_run = client.get(f"/api/v1/evaluations/{fixed['id']}").json()
    issue = s["post"](f"quality/issues/{issue['id']}/retest", {"expected_revision": issue["revision"], "grading_batch_id": fixed_run["grading_batches"][0]["id"], "note": "Fixed mapping verified"})
    assert issue["status"] == "resolved"
    detail = client.get(f"/api/v1/quality/issues/{issue['id']}").json()
    assert detail["evidence"]["cause_status"] == "awaiting_developer_confirmation"
    assert detail["confirmed_cause"] == "Verified output status mapping"
    assert any(e["action"] == "retest" for e in detail["events"])
    assert client.get(f"/api/v1/evaluations/{run['id']}/report").json()["success_rate"] == .5


def test_failed_retest_stays_open_and_changed_labels_are_rejected(client, setup_run):
    s, run, issue = create_failed_case(client, setup_run)
    issue = move_to_retest(s, issue)
    body = {"expected_revision": issue["revision"], "grading_batch_id": run["grading_batch_id"], "note": "original failure"}
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/retest", json=body).status_code == 422
    bad_again = s["post"]("evaluations", {**s["body"], "agent_version_id": run["agent_version_id"]})
    from agenteval.db import GradingBatch
    with client.app.state.sessions() as session:
        batch = session.get(GradingBatch, bad_again["grading_batch_id"])
        frozen = deepcopy(batch.grading_manifest)
        batch.grading_manifest = {**frozen, "judge": {**frozen["judge"], "model": "different-judge"}}
        session.commit()
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/retest", json={**body, "grading_batch_id": bad_again["grading_batch_id"]}).status_code == 422
    with client.app.state.sessions() as session:
        session.get(GradingBatch, bad_again["grading_batch_id"]).grading_manifest = frozen
        session.commit()
    value = s["post"](f"quality/issues/{issue['id']}/retest", {**body, "grading_batch_id": bad_again["grading_batch_id"]})
    assert value["status"] == "fix_in_progress"
    value = s["post"](f"quality/issues/{issue['id']}/update", {"expected_revision": value["revision"], "status": "ready_for_retest", "note": "new fix"})
    changed = deepcopy(s["data"])
    changed["version"] = "changed-label"
    for case in changed["cases"]:
        case["assertions"]["expected_output"] = {"order_id": "anything", "status": "anything"}
    bv = s["post"](f"benchmarks/{s['benchmark']['id']}/versions", changed)
    retest = s["post"]("evaluations", {**s["body"], "benchmark_version_id": bv["id"]})
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/retest", json={**body, "expected_revision": value["revision"], "grading_batch_id": retest["grading_batch_id"]}).status_code == 422


def test_regression_promotion_is_versioned_idempotent_and_does_not_edit_source(client, setup_run):
    s, run, issue = create_failed_case(client, setup_run)
    one = s["post"](f"quality/issues/{issue['id']}/regression-case", {})
    two = s["post"](f"quality/issues/{issue['id']}/regression-case", {})
    assert one["id"] == two["id"] and one["case_count"] == 1
    source = client.get(f"/api/v1/benchmark-versions/{s['benchmark_version']['id']}/cases").json()["items"]
    assert len(source) == 2 and all('regression' not in c["definition"]["tags"] for c in source)
    promoted = client.get(f"/api/v1/benchmark-versions/{one['id']}/cases").json()["items"]
    assert 'regression' in promoted[0]["definition"]["tags"]
    repeated = s["post"]("evaluations", {**s["body"], "agent_version_id": run["agent_version_id"]})
    repeated_issue = s["post"]("quality/issues/import", {"grading_batch_id": repeated["grading_batch_id"]})["items"][0]
    reused = s["post"](f"quality/issues/{repeated_issue['id']}/regression-case", {})
    assert reused["id"] == one["id"]


def test_release_three_states_immutable_export_and_new_grading_evidence(client, setup_run):
    s = setup_run
    a = s["post"]("evaluations", s["body"])
    b = s["post"]("evaluations", s["body"])
    comparison = s["post"]("regressions/compare", {"baseline_batch_id": a["grading_batch_id"], "candidate_batch_id": b["grading_batch_id"]})
    from agenteval.db import GradingBatch
    with client.app.state.sessions() as session:
        batch = session.get(GradingBatch, a["grading_batch_id"])
        original = deepcopy(batch.grading_manifest)
        batch.grading_manifest = {**original, "engine_version": "old-engine"}
        session.commit()
    assert client.post("/api/v1/quality/issues/import", json={"grading_batch_id": a["grading_batch_id"]}).status_code == 409
    old = s["post"]("release-decisions", {"comparison_id": comparison["id"]})
    assert old["status"] == "insufficient" and "ENGINE_REASSESSMENT_REQUIRED" in old["snapshot"]["reasons"]
    with client.app.state.sessions() as session:
        session.get(GradingBatch, a["grading_batch_id"]).grading_manifest = original
        session.commit()
    ready = s["post"]("release-decisions", {"comparison_id": comparison["id"], "note": "<script>untrusted</script>"})
    assert ready["status"] == "ready" and ready["snapshot"]["evidence_complete"]
    exported = client.get(f"/api/v1/release-decisions/{ready['id']}/export?format=html")
    assert '<script>untrusted</script>' not in exported.text and '&lt;script&gt;' in exported.text
    assert client.get(f"/api/v1/release-decisions/{ready['id']}/export").json()["content_hash"]
    s["post"](f"evaluations/{b['id']}/regrade", {})
    newer = s["post"]("release-decisions", {"comparison_id": comparison["id"]})
    assert newer["status"] == "insufficient" and "NEWER_GRADING_BATCH_EXISTS" in newer["snapshot"]["reasons"]
    assert client.get(f"/api/v1/release-decisions/{ready['id']}").json()["status"] == "ready"
    explore = s["post"]("regressions/compare", {"baseline_batch_id": a["grading_batch_id"], "candidate_batch_id": b["grading_batch_id"], "mode": "exploratory"})
    assert s["post"]("release-decisions", {"comparison_id": explore["id"]})["status"] == "insufficient"
    _, bad, issue = create_failed_case(client, s)
    failed = s["post"]("regressions/compare", {"baseline_batch_id": a["grading_batch_id"], "candidate_batch_id": bad["grading_batch_id"]})
    assert s["post"]("release-decisions", {"comparison_id": failed["id"]})["status"] == "blocked"


def test_workbench_counts_are_project_scoped_and_not_current_resource_page(client, setup_run):
    s, run, issue = create_failed_case(client, setup_run)
    value = client.get(f"/api/v1/quality/workbench/{s['project']['id']}").json()
    assert value["open_issues"] == 0 and value["pending_anomalies"] == 1 and value["priorities"][0]["id"] == issue["id"]
    other = s["post"]("projects", {"name": "Other"})
    assert client.get(f"/api/v1/quality/workbench/{other['id']}").json()["open_issues"] == 0
    assert client.get(f"/api/v1/quality/comparisons?project_id={other['id']}").json()["total"] == 0


def test_unresolved_blocker_prevents_release_until_retest_and_snapshot_stays_frozen(client, setup_run):
    s, bad, issue = create_failed_case(client, setup_run)
    with client.app.state.sessions() as session:
        session.get(QualityIssue, issue["id"]).severity = "blocking"
        session.commit()
    a = s["post"]("evaluations", s["body"])
    b = s["post"]("evaluations", s["body"])
    comparison = s["post"]("regressions/compare", {"baseline_batch_id": a["grading_batch_id"], "candidate_batch_id": b["grading_batch_id"]})
    assert comparison["report"]["gate"]["status"] == "passed"
    issue = move_to_retest(s, issue)
    issue = s["post"](f"quality/issues/{issue['id']}/scope", {"expected_revision": issue["revision"],
        "version_ids": [bad["agent_version_id"], b["agent_version_id"]], "note": "Explicitly verified affected versions"})
    blocked = s["post"]("release-decisions", {"comparison_id": comparison["id"]})
    assert blocked["status"] == "blocked" and blocked["snapshot"]["blocking_issues"][0]["id"] == issue["id"]
    issue = s["post"](f"quality/issues/{issue['id']}/retest", {"expected_revision": issue["revision"], "grading_batch_id": b["grading_batch_id"], "note": "all corresponding trials passed"})
    assert issue["status"] == "resolved"
    assert s["post"]("release-decisions", {"comparison_id": comparison["id"]})["status"] == "ready"
    assert client.get(f"/api/v1/release-decisions/{blocked['id']}").json()["status"] == "blocked"


def test_false_business_success_has_observed_facts_and_unconfirmed_cause(client):
    body = guided_body()
    body["agent"]["configuration"]["plugin_id"] = "example.refund.liar"
    body["goals"] = ["answer", "state"]
    body["environment_id"] = "sqlite.refund.v1"
    body["benchmark"]["cases"][0] = {"case_key": "refund", "input": {"order_id": "1001"}, "assertions": {
        "expected_output": {"order_id": "1001", "status": "requested"},
        "state": {"checks": [{"path": "/refunds", "expected": [{"order_id": "1001", "status": "requested"}]}]}}}
    run = client.post("/api/v1/onboarding/evaluations", json=body).json()
    imported = client.post("/api/v1/quality/issues/import", json={"grading_batch_id": run["grading_batch_id"]}).json()
    issue = imported["items"][0]
    assert issue["category"] == "output_state_mismatch" and not issue["confirmed_cause"]
    assert issue["evidence"]["cause_status"] == "awaiting_developer_confirmation"
    assert issue["evidence"]["environment_evidence"]["state"]["refunds"] == []
    # Passing a JSON Schema proves shape, not a correct success claim.
    from agenteval.db import EvaluationResult, Execution, TestCase
    from agenteval.quality import diagnose
    with client.app.state.sessions() as session:
        scores = list(session.scalars(select(EvaluationResult).filter_by(batch_id=run["grading_batch_id"])))
        output = next(r for r in scores if r.result["evaluator_id"] == "exact_match")
        output.result = {**output.result, "evaluator_id": "json_schema"}
        case = session.get(TestCase, issue["case_id"])
        execution = session.get(Execution, issue["execution_id"])
        assert diagnose(case, execution, scores, [])[0] == "state_verification"


def test_historical_hard_constraint_is_not_lost_when_canonical_attempt_passes(client, setup_run):
    from agenteval.db import Execution, EvaluationResult, TrialAssessment
    s = setup_run
    run = s["post"]("evaluations", s["body"])
    with client.app.state.sessions() as session:
        assessment = session.scalar(select(TrialAssessment).filter_by(batch_id=run["grading_batch_id"]))
        original = session.get(Execution, assessment.selected_execution_id)
        retry = Execution(trial_id=original.trial_id, attempt_index=1, status="completed", response=original.response)
        session.add(retry); session.flush()
        results = list(session.scalars(select(EvaluationResult).filter_by(batch_id=run["grading_batch_id"], execution_id=original.id)))
        for value in results:
            session.add(EvaluationResult(batch_id=value.batch_id, execution_id=retry.id, instance_id=value.instance_id, result=deepcopy(value.result)))
        hard = next(r for r in results if r.result["hard_constraint"])
        hard.result = {**hard.result, "passed": False, "score": 0}
        assessment.selected_execution_id, assessment.verdict, assessment.reasons = retry.id, "failed", [hard.instance_id]
        session.commit()
    issue = s["post"]("quality/issues/import", {"grading_batch_id": run["grading_batch_id"]})["items"][0]
    assert issue["severity"] == "blocking"
    assert any(f.get("historical") and f.get("passed") is False for f in issue["evidence"]["facts"])
