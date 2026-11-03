"""Create inspectable state-verification examples through public APIs."""


def create_demo(client):
    def post(path, value):
        reply = client.post("/api/v1/" + path, json=value)
        reply.raise_for_status()
        return reply.json()
    project = post("projects", {"name": "AgentEval 0.5 可信评测演示", "description": "独立状态探针识别假报成功；本地 Python 与 LangGraph 集成样例。"})
    bm = post("benchmarks", {"project_id": project["id"], "name": "退款状态反例"})
    bv = post(f"benchmarks/{bm['id']}/versions", {"version": "1.0", "cases": [
        {"case_key": f"refund-{key}", "input": {"order_id": key}, "assertions": {
            "expected_output": {"order_id": key, "status": "requested"},
            "state": {"checks": [{"path": "/refunds", "expected": [{"order_id": key, "status": "requested"}]}]}}}
        for key in ("1001", "1002")]})
    profile = post("profile-versions", {"project_id": project["id"], "name": "输出 + 独立状态", "version": "1.0",
        "environment": {"adapter_id": "sqlite.refund.v1"}, "evaluators": [
            {"instance_id": "answer", "id": "exact_match"}, {"instance_id": "state", "id": "state_assertion"}]})
    runs = []
    for kind, plugin in (("python", "example.refund.python"), ("python", "example.refund.liar"), ("langgraph", "example.refund.graph")):
        agent = post("agents", {"project_id": project["id"], "name": plugin, "adapter_type": kind})
        version = post(f"agents/{agent['id']}/versions", {"version": "1.0", "configuration": {"plugin_id": plugin}})
        runs.append(post("evaluations", {"agent_version_id": version["id"], "benchmark_version_id": bv["id"], "profile_version_id": profile["id"]}))
    # Local mode API responses settle these runs; remote callers should wait for completion before comparing.
    return {"project": project, "runs": runs}
