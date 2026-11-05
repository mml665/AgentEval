"""A public-API product walkthrough using local state fixtures, not production Agents."""
from uuid import uuid4
from agenteval import __version__


def create_product_demo(client):
    def post(path, value):
        reply = client.post("/api/v1/" + path, json=value)
        reply.raise_for_status()
        return reply.json()

    def wait(run):
        import time
        for _ in range(300):
            value = client.get(f"/api/v1/evaluations/{run['id']}").json()
            if value["status"] in ("completed", "cancelled") and all(b["status"] == "completed" for b in value["grading_batches"]):
                return value
            time.sleep(.2)
        raise RuntimeError("Product demonstration did not settle")

    bad = post("onboarding/evaluations", {"request_key": str(uuid4()), "project_name": f"AgentEval {__version__} 产品闭环演示", "agent_name": "退款助手", "adapter_type": "python",
        "agent": {"version": "2.0", "configuration": {"plugin_id": "example.refund.liar"}}, "goals": ["answer", "state"],
        "environment_id": "sqlite.refund.v1", "gate": {"core_case_keys": ["refund-1001"]},
        "benchmark": {"version": "1.0", "cases": [{"case_key": "refund-1001", "input": {"order_id": "1001"}, "assertions": {
            "expected_output": {"order_id": "1001", "status": "requested"},
            "state": {"checks": [{"path": "/refunds", "expected": [{"order_id": "1001", "status": "requested"}]}]}}}]}})
    bad = wait(bad)
    av = client.get(f"/api/v1/agents?project_id={bad['project_id']}").json()["items"][0]
    baseline_version = post(f"agents/{av['id']}/versions", {"version": "1.0", "configuration": {"plugin_id": "example.refund.python"}})
    baseline = wait(post("evaluations", {"agent_version_id": baseline_version["id"], "benchmark_version_id": bad["benchmark_version_id"], "profile_version_id": bad["profile_version_id"]}))
    issue = post("quality/issues/import", {"grading_batch_id": bad["grading_batches"][0]["id"]})["items"][0]
    post("quality/baselines/" + bad["project_id"], {"agent_id": av["id"], "benchmark_version_id": bad["benchmark_version_id"], "grading_batch_id": baseline["grading_batches"][0]["id"]})
    failed_compare = post("regressions/compare", {"baseline_batch_id": baseline["grading_batches"][0]["id"], "candidate_batch_id": bad["grading_batches"][0]["id"]})
    blocked = post("release-decisions", {"comparison_id": failed_compare["id"], "note": "实际业务状态失败，阻断该版本。"})
    assert blocked["status"] == "blocked"
    for status in ("confirmed", "fix_in_progress", "ready_for_retest"):
        issue = post(f"quality/issues/{issue['id']}/update", {"expected_revision": issue["revision"], "status": status, "owner": "演示开发者",
            "confirmed_cause": "本地反例仅生成成功消息，没有执行退款写入。", "note": f"演示处理：{status}"})
    fixed_version = post(f"agents/{av['id']}/versions", {"version": "3.0", "configuration": {"plugin_id": "example.refund.python"}})
    fixed = wait(post(f"quality/issues/{issue['id']}/evaluations", {"agent_version_id": fixed_version["id"], "request_key": str(uuid4())}))
    issue = post(f"quality/issues/{issue['id']}/retest", {"expected_revision": issue["revision"], "grading_batch_id": fixed["grading_batches"][0]["id"], "note": "相同测试集和评分方案，全部 Trial 通过。"})
    assert issue["status"] == "resolved"
    regression = post(f"quality/issues/{issue['id']}/regression-case", {})
    fixed_compare = post("regressions/compare", {"baseline_batch_id": baseline["grading_batches"][0]["id"], "candidate_batch_id": fixed["grading_batches"][0]["id"]})
    ready = post("release-decisions", {"comparison_id": fixed_compare["id"], "note": "本地固定样例复测与证据通过，仅作为集成验证。"})
    assert ready["status"] == "ready"
    exploratory = post("regressions/compare", {"baseline_batch_id": baseline["grading_batches"][0]["id"], "candidate_batch_id": fixed["grading_batches"][0]["id"], "mode": "exploratory"})
    insufficient = post("release-decisions", {"comparison_id": exploratory["id"], "note": "探索比较不产生可发布结论。"})
    assert insufficient["status"] == "insufficient"
    # Leave the current workbench on a compatible comparison and an explicit saved assessment.
    final_compare = post("regressions/compare", {"baseline_batch_id": baseline["grading_batches"][0]["id"], "candidate_batch_id": fixed["grading_batches"][0]["id"]})
    final = post("release-decisions", {"comparison_id": final_compare["id"]})
    return {"project_id": bad["project_id"], "issue_id": issue["id"], "source_run_id": bad["id"],
            "baseline_run_id": baseline["id"], "fixed_run_id": fixed["id"], "regression_version_id": regression["id"],
            "decisions": {"blocked": blocked["id"], "ready": final["id"], "insufficient": insufficient["id"]}}
