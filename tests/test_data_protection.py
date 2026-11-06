from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from agenteval.contracts import digest
from agenteval.db import Execution, Run, SecurityAudit, TraceSpan
from agenteval.protection import MARKER, ViewPolicy, redact
from test_team_security import team, resources


@pytest.mark.parametrize("sample", [
    {"nested": {"password": "plain-sensitive"}},
    {"message": "failed password=plain-sensitive while executing"},
    {"message": "failed api_key=plain-sensitive&mode=one"},
    {"message": "https://user:plain-sensitive@agent.test/path"},
    {"message": "Authorization: Bearer plain-sensitive"},
    {"event": json.dumps({"nested": [{"secret": "plain-sensitive"}]})},
    {"message": "user=person@example.com"},
    {"person@example.com": "metadata"},
])
def test_default_redaction_covers_nested_text_urls_encoded_json_and_keys(sample):
    result = json.dumps(redact(sample))
    assert "plain-sensitive" not in result and "person@example.com" not in result
    assert MARKER in result


def test_paths_are_wildcards_and_original_values_remain_unchanged():
    original = {"a": [{"customer": {"phone": "13800000000", "city": "Shanghai"}}]}
    result = redact(original, ViewPolicy(paths=("/**/customer/phone",)))
    assert result["a"][0]["customer"]["phone"] == MARKER
    assert original["a"][0]["customer"]["phone"] == "13800000000"
    assert result["a"][0]["customer"]["city"] == "Shanghai"


def test_business_views_exports_and_storage_share_policy_without_modifying_evidence(team, monkeypatch):
    t = team; a = resources(t, t["a"])
    secret = "customer-secret-with/slash+symbol"
    monkeypatch.setenv("AGENTEVAL_REDACTION_SECRET_TEST", secret)
    t["post"]("protection/policy/" + t["a"]["id"], {"paths": ["/**/customer/phone"], "secret_env_names": ["AGENTEVAL_REDACTION_SECRET_TEST"], "retention_days": 30})
    with t["app"].state.sessions() as session:
        execution = session.get(Execution, a["execution_id"])
        execution.error = {"message": "failure url=https://example.test/?value=" + quote(secret, safe=""), "customer": {"phone": "13800000000"}}
        execution.response = {**execution.response, "business_note": secret, "customer": {"phone": "13800000000"}}
        original = deepcopy(execution.response)
        for span in session.scalars(select(TraceSpan).where(TraceSpan.execution_id == execution.id)):
            span.record = {**span.record, "attributes": {"encoded": json.dumps({"private_note": secret})}}
        session.commit()
        manifest_hash = session.get(Run, a["run"]["id"]).manifest_hash
    c = t["reader"]["client"]
    for path in (f"executions/{a['execution_id']}", f"executions/{a['execution_id']}/trace", f"traces/{original['trace_spans'][0]['trace_id']}" if original.get("trace_spans") else f"executions/{a['execution_id']}"):
        r = c.get("/api/v1/" + path)
        assert r.status_code == 200, r.text
        assert secret not in r.text and quote(secret, safe="") not in r.text and "13800000000" not in r.text
        assert r.headers["x-agenteval-view"] == "redacted"
        assert hashlib.sha256(r.content).hexdigest() == r.headers["x-agenteval-view-hash"]
    exported = c.get(f"/api/v1/evaluations/{a['run']['id']}/export?grading_batch_id={a['run']['grading_batch_id']}")
    value = exported.json(); checksum = value.pop("content_hash")
    assert digest(value) == checksum and value["view"]["kind"] == "redacted"
    assert c.get(f"/api/v1/protection/storage/{t['a']['id']}").json()["automatic_deletion"] is False
    with t["app"].state.sessions() as session:
        assert session.get(Execution, a["execution_id"]).response == original
        assert session.get(Run, a["run"]["id"]).manifest_hash == manifest_hash
    monkeypatch.setenv("AGENTEVAL_REDACTION_SECRET_TEST", "unexpected-rotation")
    assert c.get(f"/api/v1/executions/{a['execution_id']}").status_code == 503
    assert t["admin"].get(f"/api/v1/protection/policy/{t['a']['id']}").status_code == 200


def test_raw_read_requires_personal_grant_reason_scope_and_pre_disclosure_audit(team):
    t = team; a = resources(t, t["a"]); reader = t["reader"]["client"]
    path = f"/api/v1/protection/evaluations/{a['run']['id']}/raw"
    reason = {"reason": "Inspect original evidence in controlled test"}
    assert reader.post(path, json=reason).status_code == 403
    assert t["developer"]["client"].post(path, json=reason).status_code == 403
    assert t["outsider"]["client"].post(path, json=reason).status_code == 404
    assert reader.get(f"/api/v1/executions/{a['execution_id']}?raw=true").headers["x-agenteval-view"] == "redacted"
    t["post"]("protection/grants/" + t["a"]["id"], {"user_id": t["reader"]["user"]["id"], "raw_read": True})
    assert reader.post(path, json={"reason": " " * 10}).status_code == 422
    result = reader.post(path, json=reason)
    assert result.status_code == 200 and result.headers["x-agenteval-view"] == "raw-audited"
    assert result.json()["view"]["content_hash"] == digest(result.json()["evidence"])
    with t["app"].state.sessions() as session:
        audit = session.get(SecurityAudit, result.json()["view"]["audit_id"])
        assert audit.actor["id"] == t["reader"]["user"]["id"] and audit.resource_ids["reason"] == reason["reason"]
    t["post"]("protection/grants/" + t["a"]["id"], {"user_id": t["reader"]["user"]["id"], "raw_read": False})
    assert reader.post(path, json=reason).status_code == 403
    token = t["post"]("team/machines", {"name": "No original access", "project_ids": [t["a"]["id"]], "permission": "evaluate"})
    with TestClient(t["app"], headers={"Authorization": "Bearer " + token["token"]}) as machine:
        assert machine.post(path, json=reason).status_code == 403


def test_policy_versions_cannot_be_overwritten_and_are_admin_only(team):
    t = team; path = "/api/v1/protection/policy/" + t["a"]["id"]
    assert t["developer"]["client"].post(path, json={}).status_code == 403
    assert t["admin"].post(path, json={"secret_env_names": ["AGENTEVAL_API_TOKEN"]}).status_code == 422
    first = t["post"]("protection/policy/" + t["a"]["id"], {})
    assert t["admin"].post(path, json={}).status_code == 409
    second = t["post"]("protection/policy/" + t["a"]["id"], {"expected_revision": 1, "redact_emails": False})
    assert first["id"] != second["id"] and second["revision"] == 2


def test_project_inventory_counts_bytes_and_pages_retention_without_double_counting_releases(team):
    t = team
    project_id = t["a"]["id"]
    source = resources(t, t["a"])
    held = source["run"]["id"]
    unheld = t["post"]("evaluations", source["body"])["id"]
    t["post"]("release-decisions", {"comparison_id": source["comparison"]["id"]})
    t["post"]("protection/policy/" + project_id, {"retention_days": 30, "evidence_warning_bytes": 1})
    # A legacy policy client can publish another revision without erasing the newer field.
    t["post"]("protection/policy/" + project_id, {"expected_revision": 1, "retention_days": 30})

    reader = t["reader"]["client"]
    endpoint = "/api/v1/protection/storage/" + project_id
    before = reader.get(endpoint).json()
    assert before["past_retention_runs"] == 0 and before["release_referenced_runs"] == 1
    assert before["warning_reached"] and before["evidence_warning_bytes"] == 1
    old = datetime.now(timezone.utc) - timedelta(days=31)
    with t["app"].state.sessions() as session:
        for run_id in (held, unheld):
            session.get(Run, run_id).created_at = old
        # Persist literal multibyte JSON in SQLite to distinguish bytes from characters.
        payload = '{"content":"证据"}'
        query = text("UPDATE case_executions SET response = :payload WHERE id = :identifier")
        session.execute(query, {"payload": payload, "identifier": source["execution_id"]})
        session.commit()
        original_bytes = session.scalar(text("SELECT length(CAST(response AS BLOB)) FROM case_executions WHERE id = :identifier"),
            {"identifier": source["execution_id"]})
    assert original_bytes == len(payload.encode("utf-8")) > len(payload)
    inventory = reader.get(endpoint).json()
    assert (inventory["runs"], inventory["past_retention_runs"], inventory["past_retention_release_holds"]) == (2, 2, 1)
    assert inventory["evidence_json_bytes"] == sum(inventory["evidence_json_bytes_by_kind"].values())
    assert inventory["evidence_json_bytes_by_kind"]["responses"] + inventory["evidence_json_bytes_by_kind"]["trace"] > inventory["response_and_trace_json_characters"]
    pages = [reader.get(endpoint + "/runs", params={"limit": 1, "offset": index}).json() for index in range(2)]
    assert [page["total"] for page in pages] == [2, 2]
    assert {item["id"]: item["release_referenced"] for page in pages for item in page["items"]} == {held: True, unheld: False}
    referenced = reader.get(endpoint + "/runs", params={"review": "referenced"}).json()
    assert referenced["total"] == 1 and referenced["items"][0]["id"] == held
    assert t["outsider"]["client"].get(endpoint + "/runs").status_code == 404
    t["post"]("protection/policy/" + project_id, {"expected_revision": 2, "evidence_warning_bytes": None})
    cleared = reader.get(endpoint).json()
    assert cleared["evidence_warning_bytes"] is None and not cleared["warning_reached"]
    assert cleared["past_retention_runs"] == 0 and cleared["release_referenced_runs"] == 1


def test_unexpected_api_exception_never_logs_or_returns_original_payload(team, caplog):
    t = team
    secret = "fixture-business-exception-plaintext"
    @t["app"].get("/api/v1/protection-test-failure")
    def fail(): raise RuntimeError(secret)
    response = t["admin"].get("/api/v1/protection-test-failure")
    assert response.status_code == 500 and response.json()["code"] == "REQUEST_FAILED"
    assert secret not in response.text and secret not in caplog.text
    assert "RuntimeError" in caplog.text
