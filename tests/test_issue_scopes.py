"""Cross-Agent, cross-version and accountable anomaly triage regressions."""
from copy import deepcopy

from sqlalchemy import select

from agenteval.db import QualityIssue
from tests.test_product_workflow import create_failed_case


def scope(s, version=None, agent=None, benchmark=None, baseline=None):
    return {"agent_id": agent or s["agent"]["id"], "candidate_version_id": version or s["version"]["id"],
        "benchmark_version_id": benchmark or s["benchmark_version"]["id"], **({"baseline_version_id": baseline} if baseline else {})}


def get(client, path, s, params=None):
    reply = client.get("/api/v1/" + path, params={"project_id": s["project"]["id"], **(params or {})})
    assert reply.status_code == 200, reply.text
    return reply.json()


def bulk(s, rows, action, **extra):
    return s["post"]("quality/issues/bulk", {"project_id": s["project"]["id"], "issue_ids": [i["id"] for i in rows],
        "expected_revisions": {i["id"]: i["revision"] for i in rows}, "action": action, "note": "Verified triage", **extra})["items"]


def test_automatic_anomaly_is_not_a_formal_defect_and_manual_mode_is_retained(client, setup_run):
    s = setup_run
    assert get(client, f"quality/policy/{s['project']['id']}", s)["ingestion_mode"] == "automatic"
    bad = s["post"]("evaluations", {**s["body"], "agent_version_id": s["post"](f"agents/{s['agent']['id']}/versions",
        {"version": "bad", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": s["version"]["capabilities"]})["id"]})
    rows = get(client, "quality/issues", s)["items"]
    assert len(rows) == 1 and rows[0]["kind"] == "anomaly" and rows[0]["status"] == "open"
    assert rows[0]["issue_scope"]["version_ids"] == [bad["agent_version_id"]]
    assert get(client, f"quality/workbench/{s['project']['id']}", s)["open_issues"] == 0
    s["post"](f"quality/policy/{s['project']['id']}", {"ingestion_mode": "manual"})
    second = s["post"]("evaluations", {**s["body"], "agent_version_id": bad["agent_version_id"]})
    assert get(client, "quality/issues", s)["total"] == 1
    imported = s["post"]("quality/issues/import", {"grading_batch_id": second["grading_batch_id"]})["items"]
    assert len(imported) == 1 and imported[0]["kind"] == "anomaly"
    ignored = bulk(s, imported, "ignore")[0]
    again = s["post"]("quality/issues/import", {"grading_batch_id": second["grading_batch_id"]})["items"][0]
    assert again["id"] == ignored["id"] and again["status"] == "ignored"
    s["post"](f"quality/policy/{s['project']['id']}", {"ingestion_mode": "automatic"})
    third = s["post"]("evaluations", {**s["body"], "agent_version_id": bad["agent_version_id"]})
    assert any(i["batch_id"] == third["grading_batch_id"] and i["status"] == "open" for i in get(client, "quality/issues", s)["items"])


def test_cross_agent_workbench_issues_and_release_decisions_never_mix(client, setup_run):
    s, bad, issue = create_failed_case(client, setup_run)
    bulk(s, [issue], "confirm")
    good = s["post"]("evaluations", s["body"])
    first_compare = s["post"]("regressions/compare", {"baseline_batch_id": good["grading_batch_id"], "candidate_batch_id": bad["grading_batch_id"]})
    first_decision = s["post"]("release-decisions", {"comparison_id": first_compare["id"]})
    agent2 = s["post"]("agents", {"project_id": s["project"]["id"], "name": "Separate implementation", "adapter_type": "python"})
    av2 = s["post"](f"agents/{agent2['id']}/versions", {"version": "bad", "configuration": s["version"]["configuration"], "capabilities": s["version"]["capabilities"]})
    r2 = s["post"]("evaluations", {**s["body"], "agent_version_id": av2["id"]})
    c2 = s["post"]("regressions/compare", {"baseline_batch_id": r2["grading_batch_id"], "candidate_batch_id": r2["grading_batch_id"]})
    d2 = s["post"]("release-decisions", {"comparison_id": c2["id"]})
    assert d2["status"] == "ready"
    params1 = scope(s, bad["agent_version_id"], baseline=good["agent_version_id"])
    params2 = scope(s, av2["id"], agent2["id"], baseline=av2["id"])
    w1 = get(client, f"quality/workbench/{s['project']['id']}", s, params1)
    w2 = get(client, f"quality/workbench/{s['project']['id']}", s, params2)
    assert w1["open_issues"] == 1 and w1["candidate"]["success_rate"] == .5 and w1["latest_decision"]["id"] == first_decision["id"]
    assert w2["open_issues"] == 0 and w2["pending_anomalies"] == 0 and w2["priorities"] == []
    assert w2["candidate"]["success_rate"] == 1 and w2["latest_decision"]["id"] == d2["id"]
    assert get(client, "quality/issues", s, params2)["total"] == 0
    assert [d["id"] for d in get(client, "release-decisions", s, params2)["items"]] == [d2["id"]]
    assert get(client, f"quality/project-overview/{s['project']['id']}", s)["open_issues"] == 1
    # Crossing identity remains useful as a comparison but cannot justify a release.
    cross = s["post"]("regressions/compare", {"baseline_batch_id": good["grading_batch_id"], "candidate_batch_id": r2["grading_batch_id"]})
    decision = s["post"]("release-decisions", {"comparison_id": cross["id"]})
    assert decision["status"] == "insufficient" and "AGENT_SCOPE_MISMATCH" in decision["snapshot"]["reasons"]


def test_historical_issue_only_blocks_explicitly_affected_versions(client, setup_run):
    s, bad, issue = create_failed_case(client, setup_run)
    with client.app.state.sessions() as session:
        session.get(QualityIssue, issue["id"]).severity = "blocking"
        session.commit()
    good = s["post"]("evaluations", s["body"])
    comparison = s["post"]("regressions/compare", {"baseline_batch_id": good["grading_batch_id"], "candidate_batch_id": good["grading_batch_id"]})
    issue = bulk(s, [issue], "confirm")[0]
    ready = s["post"]("release-decisions", {"comparison_id": comparison["id"]})
    assert ready["status"] == "ready" and not ready["snapshot"]["blocking_issues"]
    assert get(client, "quality/issues", s, scope(s))["total"] == 0
    assert get(client, "quality/issues", s, scope(s, bad["agent_version_id"]))["total"] == 1
    changed = s["post"](f"quality/issues/{issue['id']}/scope", {"expected_revision": issue["revision"],
        "version_ids": [bad["agent_version_id"], good["agent_version_id"]], "note": "Verified version scope"})
    blocked = s["post"]("release-decisions", {"comparison_id": comparison["id"]})
    assert blocked["status"] == "blocked" and blocked["snapshot"]["blocking_issues"][0]["issue_scope"]["version_ids"] == changed["issue_scope"]["version_ids"]
    assert get(client, f"release-decisions/{ready['id']}", s)["status"] == "ready"
    # Changing labels/case semantics or Benchmark identity never inherits the blocker.
    for other_benchmark in (False, True):
        data = deepcopy(s["data"]); data["version"] = "other"
        bm_id = s["benchmark"]["id"]
        if other_benchmark:
            bm_id = s["post"]("benchmarks", {"project_id": s["project"]["id"], "name": "Different benchmark"})["id"]
        else:
            for c in data["cases"]: c["assertions"]["expected_output"] = {"changed": True}
        bv = s["post"](f"benchmarks/{bm_id}/versions", data)
        assert get(client, "quality/issues", s, scope(s, benchmark=bv["id"]))["total"] == 0


def test_anomaly_never_becomes_issue_blocker_before_confirmation(client, setup_run):
    s, bad, issue = create_failed_case(client, setup_run)
    with client.app.state.sessions() as session:
        session.get(QualityIssue, issue["id"]).severity = "blocking"; session.commit()
    issue = s["post"](f"quality/issues/{issue['id']}/scope", {"expected_revision": 0, "version_ids": [s["version"]["id"]], "note": "Investigate possible affected version"})
    good = s["post"]("evaluations", s["body"])
    c = s["post"]("regressions/compare", {"baseline_batch_id": good["grading_batch_id"], "candidate_batch_id": good["grading_batch_id"]})
    assert s["post"]("release-decisions", {"comparison_id": c["id"]})["status"] == "ready"
    issue = bulk(s, [issue], "confirm")[0]
    assert s["post"]("release-decisions", {"comparison_id": c["id"]})["status"] == "blocked"


def test_bulk_merge_keeps_sources_scope_union_and_audit_and_assignment(client, setup_run):
    s, bad, issue = create_failed_case(client, setup_run)
    av = s["post"](f"agents/{s['agent']['id']}/versions", {"version": "bad-again", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": s["version"]["capabilities"]})
    run = s["post"]("evaluations", {**s["body"], "agent_version_id": av["id"]})
    second = s["post"]("quality/issues/import", {"grading_batch_id": run["grading_batch_id"]})["items"][0]
    first = bulk(s, [issue], "confirm")[0]
    assigned = bulk(s, [first, second], "assign", owner="Engineer")
    merged = bulk(s, assigned, "merge", target_issue_id=second["id"])
    target = get(client, f"quality/issues/{second['id']}", s)
    source = get(client, f"quality/issues/{first['id']}", s)
    assert target["kind"] == "issue" and target["status"] == "confirmed" and target["owner"] == "Engineer"
    assert set(target["issue_scope"]["version_ids"]) == {bad["agent_version_id"], av["id"]}
    assert source["status"] == "merged" and source["merged_into_id"] == target["id"] and source["evidence"]
    assert any(e["action"] == "bulk_merge" for e in source["events"])
    assert len(merged) == 2


def test_bulk_stale_revision_is_atomic_and_cross_agent_merge_scope_update_retest_rejected(client, setup_run):
    s, bad, issue = create_failed_case(client, setup_run)
    second_agent = s["post"]("agents", {"project_id": s["project"]["id"], "name": "Another", "adapter_type": "python"})
    av = s["post"](f"agents/{second_agent['id']}/versions", {"version": "bad", "configuration": {"plugin_id": "example.order.v2"}, "capabilities": s["version"]["capabilities"]})
    run = s["post"]("evaluations", {**s["body"], "agent_version_id": av["id"]})
    other = s["post"]("quality/issues/import", {"grading_batch_id": run["grading_batch_id"]})["items"][0]
    data = {"project_id": s["project"]["id"], "issue_ids": [issue["id"], other["id"]], "expected_revisions": {issue["id"]: 0, other["id"]: 99}, "action": "ignore", "note": "atomic"}
    assert client.post("/api/v1/quality/issues/bulk", json=data).status_code == 409
    assert get(client, f"quality/issues/{issue['id']}", s)["status"] == "open"
    data.update(action="merge", expected_revisions={issue["id"]: 0, other["id"]: 0}, target_issue_id=issue["id"])
    assert client.post("/api/v1/quality/issues/bulk", json=data).status_code == 422
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/scope", json={"expected_revision": 0, "version_ids": [av["id"]], "note": "cross"}).status_code == 422
    for status in ("confirmed", "fix_in_progress", "ready_for_retest"):
        issue = s["post"](f"quality/issues/{issue['id']}/update", {"expected_revision": issue["revision"], "status": status, "note": "fix"})
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/evaluations", json={"agent_version_id": av["id"], "request_key": "cross-agent-retest"}).status_code == 422
    assert client.post(f"/api/v1/quality/issues/{issue['id']}/retest", json={"expected_revision": issue["revision"], "grading_batch_id": run["grading_batch_id"], "note": "cross"}).status_code == 422


def test_scope_validation_rejects_partial_and_foreign_agent_versions(client, setup_run):
    s = setup_run
    for params in ({"agent_id": s["agent"]["id"]}, {**scope(s), "baseline_version_id": "missing"}):
        assert client.get(f"/api/v1/quality/workbench/{s['project']['id']}", params=params).status_code in (404, 422)
    other = s["post"]("agents", {"project_id": s["project"]["id"], "name": "Other", "adapter_type": "python"})
    assert client.get(f"/api/v1/quality/workbench/{s['project']['id']}", params=scope(s, agent=other["id"])).status_code == 422


def test_release_rejects_cross_benchmark_even_when_case_content_matches(client, setup_run):
    s = setup_run
    bm = s["post"]("benchmarks", {"project_id": s["project"]["id"], "name": "Other identity"})
    bv = s["post"](f"benchmarks/{bm['id']}/versions", s["data"])
    a = s["post"]("evaluations", s["body"])
    b = s["post"]("evaluations", {**s["body"], "benchmark_version_id": bv["id"]})
    body = {"baseline_batch_id": a["grading_batch_id"], "candidate_batch_id": b["grading_batch_id"]}
    assert client.post("/api/v1/regressions/compare", json=body).status_code == 409
    c = s["post"]("regressions/compare", {**body, "mode": "exploratory"})
    decision = s["post"]("release-decisions", {"comparison_id": c["id"]})
    assert decision["status"] == "insufficient" and "BENCHMARK_SCOPE_MISMATCH" in decision["snapshot"]["reasons"]


def test_legacy_upgrade_backfills_conservative_scope_and_remembers_confirmed_reopened_issue(client, setup_run):
    from alembic import command
    from alembic.config import Config
    from agenteval.db import IssueScope
    s, bad, issue = create_failed_case(client, setup_run)
    issue = s["post"](f"quality/issues/{issue['id']}/update", {"expected_revision": 0, "status": "confirmed", "note": "Confirmed before upgrade"})
    with client.app.state.sessions() as session:
        session.get(QualityIssue, issue["id"]).status = "open"  # Legacy reopened formal Issue.
        session.commit()
    other_run = s["post"]("evaluations", {**s["body"], "agent_version_id": bad["agent_version_id"]})
    client.app.state.engine.dispose()
    from pathlib import Path
    config = Config("alembic.ini"); config.attributes["database_url"] = "sqlite:///" + Path(client.app.state.engine.url.database).as_posix()
    command.stamp(config, "head")
    command.downgrade(config, "0006")
    command.upgrade(config, "head")
    with client.app.state.sessions() as session:
        legacy = session.get(QualityIssue, issue["id"])
        assert legacy.kind == "issue" and legacy.status == "open"
        scope_row = session.scalar(select(IssueScope).filter_by(issue_id=legacy.id))
        assert scope_row.agent_id == s["agent"]["id"] and scope_row.benchmark_id == s["benchmark"]["id"]
        assert scope_row.version_ids == [bad["agent_version_id"]] and scope_row.case_hash == issue["evidence"]["case_hash"]
        pending = session.scalar(select(QualityIssue).filter_by(batch_id=other_run["grading_batch_id"]))
        assert pending.kind == "anomaly"
