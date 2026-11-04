from copy import deepcopy
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from agenteval.db import EvaluationResult, Execution, GradingBatch, Run, TrialAssessment
from agenteval.evidence_summary import differences, summarize
from tests.test_product_workflow import guided_body


def test_server_search_finds_last_page_and_filters_totals_and_stable_order(client, setup_run):
    s = setup_run
    first = s["post"]("evaluations", s["body"])
    with client.app.state.sessions() as session:
        source = session.get(Run, first["id"])
        for i in range(14):
            session.add(Run(project_id=source.project_id, agent_version_id=source.agent_version_id,
                benchmark_version_id=source.benchmark_version_id, profile_version_id=source.profile_version_id,
                status="queued" if i % 2 else "completed", manifest=source.manifest, manifest_hash=source.manifest_hash,
                created_at=datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)))
        session.commit()
    def query(**params):
        r = client.get("/api/v1/evaluations", params={"project_id": s["project"]["id"], "limit": 10, **params})
        assert r.status_code == 200, r.text
        return r.json()
    page1, page2 = query(), query(offset=10)
    target = page2["items"][-1]["id"]
    assert target not in [r["id"] for r in page1["items"]]
    found = query(q=target)
    assert found["total"] == 1 and found["items"][0]["id"] == target
    assert query(q="orders")["total"] == 15
    assert query(q="Cases")["total"] == 15
    assert query(q="%_")["total"] == 0  # Literal LIKE metacharacters, not a wildcard dump.
    assert query(status="queued")["total"] == 7
    assert query(quality="passed")["total"] == 1
    assert query(quality="pending")["total"] == 14
    assert query(version="1.0")["total"] == 15 and query(version="other")["total"] == 0
    assert query(created_after="2020-01-01T00:00:00Z", created_before="2020-01-02T23:59:59Z")["total"] == 2
    assert query(sort="oldest")["items"][0]["id"] == target
    assert [r["id"] for r in query()["items"]] == [r["id"] for r in page1["items"]]
    other = s["post"]("projects", {"name": "Another project"})
    assert client.get("/api/v1/evaluations", params={"project_id": other["id"], "q": target}).json()["total"] == 0
    assert client.get("/api/v1/evaluations", params={"created_after": "2026-01-02T00:00:00Z", "created_before": "2026-01-01T00:00:00Z"}).status_code == 422


def test_execution_done_quality_failed_and_summary_matches_fixed_report(client, setup_run):
    s = setup_run
    bad = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "bad", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": s["version"]["capabilities"]})
    run = s["post"]("evaluations", {**s["body"], "agent_version_id": bad["id"]})
    row = client.get(f"/api/v1/evaluations/{run['id']}").json()
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert row["status"] == "completed" and row["quality_summary"]["quality_status"] == "failed"
    assert row["quality_summary"] == report["quality_summary"]
    assert row["quality_summary"]["initiator"]["id"] is None
    before = deepcopy(report["quality_summary"])
    s["post"](f"evaluations/{run['id']}/regrade", {})
    latest = client.get(f"/api/v1/evaluations/{run['id']}").json()["quality_summary"]
    frozen = client.get(f"/api/v1/evaluations/{run['id']}/report?grading_batch_id={run['grading_batch_id']}").json()
    assert latest["revision"] == 1 and frozen["quality_summary"] == before


def test_pending_unknown_partial_and_empty_never_display_all_passed(client, setup_run):
    s = setup_run
    run = s["post"]("evaluations", s["body"])
    def summary(): return client.get(f"/api/v1/evaluations/{run['id']}").json()["quality_summary"]
    assert summary()["quality_status"] == "passed"
    with client.app.state.sessions() as session:
        rows = list(session.scalars(select(TrialAssessment).filter_by(batch_id=run["grading_batch_id"])))
        for row in rows: row.verdict = "unknown"
        session.commit()
    assert summary()["quality_status"] == "insufficient"
    with client.app.state.sessions() as session:
        rows = list(session.scalars(select(TrialAssessment).filter_by(batch_id=run["grading_batch_id"])))
        for row in rows: row.verdict = "passed"
        score = session.scalar(select(EvaluationResult).filter_by(batch_id=run["grading_batch_id"]))
        score.result = {**score.result, "grading_status": "missing_evidence", "passed": None, "score": None}
        session.commit()
    assert summary()["quality_status"] == "insufficient" and summary()["trial_scoring_coverage"] < 1
    with client.app.state.sessions() as session:
        session.get(GradingBatch, run["grading_batch_id"]).status = "running"; session.commit()
    assert summary()["quality_status"] == "pending"


def test_latency_samples_use_frozen_gate_threshold_and_exclude_failures(client, setup_run):
    s = setup_run
    run = s["post"]("evaluations", s["body"])
    with client.app.state.sessions() as session:
        batch = session.get(GradingBatch, run["grading_batch_id"])
        batch.grading_manifest = {**batch.grading_manifest, "gate": {**batch.grading_manifest["gate"], "min_latency_samples": 9}}
        rows = list(session.scalars(select(TrialAssessment).filter_by(batch_id=batch.id)))
        rows[0].verdict = "failed"
        session.get(Execution, rows[0].selected_execution_id).status = "agent_failed"
        session.get(Execution, rows[0].selected_execution_id).latency_ms = 999
        session.get(Execution, rows[1].selected_execution_id).latency_ms = 123
        session.commit()
    r = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    assert r["latency_ms"]["min_samples"] == 9 and not r["latency_ms"]["sufficient"]
    assert r["latency_ms"]["scope"] == "canonical_completed_executions_only"
    assert r["latency_ms"]["samples"] == 1 and r["latency_ms"]["p95"] == 123 and r["latency_ms"]["excluded_trials"] == 1
    assert r["planned_trials"] == sum(r["counts"].values())


def test_deterministic_evidence_refund_diff_missing_evidence_and_foreign_batch(client):
    body = guided_body(); body["agent"]["configuration"] = {"plugin_id": "example.refund.liar"}
    body["goals"] = ["answer", "state"]; body["environment_id"] = "sqlite.refund.v1"
    body["benchmark"]["cases"][0] = {"case_key": "refund", "input": {"order_id": "1"}, "assertions": {
        "expected_output": {"order_id": "1", "status": "requested"},
        "state": {"checks": [{"path": "/refunds", "expected": [{"order_id": "1", "status": "requested"}]}]}}}
    run = client.post("/api/v1/onboarding/evaluations", json=body).json()
    report = client.get(f"/api/v1/evaluations/{run['id']}/report").json()
    execution = report["trials"][0]["execution_id"]
    url = f"/api/v1/executions/{execution}/evidence-summary"
    r = client.get(url, params={"grading_batch_id": run["grading_batch_id"]}).json()
    assert r["headline"] == "答案符合预期，但独立状态未出现预期退款记录。"
    assert r["items"][0]["rule"] == "state_assertion" and r["items"][0]["differences"][0]["actual_present"] is False
    with client.app.state.sessions() as session:
        score = next(r for r in session.scalars(select(EvaluationResult).filter_by(batch_id=run["grading_batch_id"])) if r.result["evaluator_id"] == "state_assertion")
        score.result = {**score.result, "grading_status": "missing_evidence", "passed": None, "score": None, "evidence": {"reason": "Independent snapshot unavailable"}}
        session.commit()
    missing = client.get(url, params={"grading_batch_id": run["grading_batch_id"]}).json()
    assert missing["items"][0]["verdict"] == "missing" and "业务结果仍待判定" in missing["headline"]
    body["request_key"] = "different-guided-request"; body["project_name"] = "Different"  # Unique project and batch.
    other = client.post("/api/v1/onboarding/evaluations", json=body).json()
    assert client.get(url, params={"grading_batch_id": other["grading_batch_id"]}).status_code == 404


def test_json_differences_preserve_missing_null_types_and_escape_paths():
    diff = differences({"a/b": None, "type": True, "deep": [1]}, {"a/b": None, "type": 1, "deep": [1, None]})
    assert {d["path"] for d in diff} == {"/type", "/deep/1"}
    assert next(d for d in diff if d["path"] == "/deep/1")["expected_present"] is False
    assert differences({"a/b": None}, {})[0]["path"] == "/a~1b"
    assert len(differences(list(range(100)), [], limit=20)) == 20


def test_matching_non_output_field_does_not_claim_final_answer_is_correct():
    facts = [{"kind": "exact_match", "instance_id": "custom", "status": "scored", "passed": True, "evidence": {"actual": 1, "expected": 1}},
        {"kind": "state_assertion", "instance_id": "state", "status": "scored", "passed": False,
            "evidence": {"checks": [{"path": "/refunds", "actual": [], "expected": [1], "passed": False}]}}]
    value = summarize("execution", "batch", "trace", facts, "completed", {"output": "wrong"}, {"assertions": {"expected_output": "correct"}})
    assert "答案符合预期" not in value["headline"]


def test_comparison_read_context_distinguishes_equal_rates_and_never_rewrites_snapshot(client, setup_run):
    s = setup_run
    one = s["post"]("evaluations", s["body"])
    two = s["post"]("evaluations", s["body"])
    original = s["post"]("regressions/compare", {"baseline_batch_id": one["grading_batch_id"], "candidate_batch_id": two["grading_batch_id"]})
    context = client.get(f"/api/v1/regressions/{original['id']}").json()
    assert context["report"] == original["report"]
    assert context["context"]["baseline"]["batch_id"] != context["context"]["candidate"]["batch_id"]
    assert context["context"]["baseline"]["agent_name"] == s["agent"]["name"]
    workspace = client.get(f"/api/v1/workspace/{s['project']['id']}").json()
    assert workspace["comparisons"][0]["context"] == context["context"]
