"""Deterministic, sourced facts and bounded JSON differences, never inferred causes."""
from agenteval.contracts import canonical


def differences(expected, actual, path="", limit=20):
    rows = []
    def add(pointer, want, got, expected_present=True, actual_present=True):
        if len(rows) < limit:
            rows.append({"path": pointer or "/", "expected": want, "actual": got,
                "expected_present": expected_present, "actual_present": actual_present})
    def visit(want, got, pointer, depth=0):
        if len(rows) >= limit or canonical(want) == canonical(got): return
        if depth < 12 and isinstance(want, dict) and isinstance(got, dict):
            for key in sorted(want.keys() | got.keys()):
                child = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in want or key not in got: add(child, want.get(key), got.get(key), key in want, key in got)
                else: visit(want[key], got[key], child, depth + 1)
        elif depth < 12 and isinstance(want, list) and isinstance(got, list):
            for i in range(max(len(want), len(got))):
                if len(rows) >= limit: break
                if i >= len(want) or i >= len(got): add(f"{pointer}/{i}", want[i] if i < len(want) else None, got[i] if i < len(got) else None, i < len(want), i < len(got))
                else: visit(want[i], got[i], f"{pointer}/{i}", depth + 1)
        else: add(pointer, want, got)
    visit(expected, actual, path)
    return rows


def summarize(execution_id, batch_id, trace_id, facts, execution_status=None, response=None, case_definition=None):
    titles = {"exact_match": "最终输出与参考断言", "state_assertion": "独立环境状态断言", "tool_call": "逐次工具调用规则",
        "tool_selection": "工具选择规则", "tool_arguments": "工具参数规则", "trajectory": "执行轨迹规则",
        "json_schema": "输出结构校验", "rag": "检索组件规则", "llm_judge": "模型裁判判定（需留意校准范围）"}
    items = []
    for fact in facts:
        if fact.get("kind") == "execution": continue
        state = fact.get("status")
        verdict = "passed" if state == "scored" and fact.get("passed") is True else "failed" if state == "scored" and fact.get("passed") is False else "missing"
        evidence = fact.get("evidence") or {}
        diff = []
        if fact["kind"] == "exact_match" and verdict == "failed" and "actual" in evidence and "expected" in evidence:
            diff = differences(evidence["expected"], evidence["actual"])
        elif fact["kind"] == "state_assertion" and verdict == "failed":
            for check in evidence.get("checks", []):
                if check.get("passed") is False:
                    if check.get("present") is False:
                        diff.append({"path": check["path"], "expected": check.get("expected"), "actual": None, "actual_present": False, "expected_present": True})
                    else: diff.extend(differences(check.get("expected"), check.get("actual"), check["path"], max(0, 20 - len(diff))))
                if len(diff) >= 20: break
        explanation = ("已观察到规则不满足。" if verdict == "failed" else "规则通过。" if verdict == "passed" else
            "观测缺失或评分未形成有效判定；不能据此断言业务失败。")
        if evidence.get("reason") and verdict == "missing": explanation += " " + str(evidence["reason"])[:300]
        items.append({"instance_id": fact.get("instance_id"), "rule": fact["kind"], "title": titles.get(fact["kind"], fact["kind"]),
            "verdict": verdict, "explanation": explanation, "differences": diff, "historical": fact.get("historical", False),
            "source_execution_id": fact.get("source_execution_id", execution_id)})
    items.sort(key=lambda i: {"failed": 0, "missing": 1, "passed": 2}[i["verdict"]])
    current = [i for i in items if not i["historical"]]
    state_failed = any(i["rule"] == "state_assertion" and i["verdict"] == "failed" for i in current)
    response, case_definition = response or {}, case_definition or {}
    assertions = case_definition.get("assertions", {})
    output_ok = "output" in response and "expected_output" in assertions and canonical(response["output"]) == canonical(assertions["expected_output"]) and any(f["kind"] == "exact_match" and f.get("passed") is True and f.get("status") == "scored" and not f.get("historical")
        and "actual" in (f.get("evidence") or {}) and "expected" in f["evidence"]
        and canonical(f["evidence"]["actual"]) == canonical(response["output"])
        and canonical(f["evidence"]["expected"]) == canonical(assertions["expected_output"]) for f in facts)
    refund = any(d["path"].startswith("/refunds") for i in current if i["rule"] == "state_assertion" for d in i["differences"])
    headline = ("答案符合预期，但独立状态未出现预期退款记录。" if refund else "答案符合预期，但独立状态未满足预期断言。") if state_failed and output_ok else (
        "已观察到规则失败，请核对预期与实际差异。" if any(i["verdict"] == "failed" for i in items) else
        "缺少有效评分证据，业务结果仍待判定。" if any(i["verdict"] == "missing" for i in items) or not items else "当前固定批次的规则均通过。")
    if execution_status in ("timed_out", "outcome_unknown"):
        headline = "执行超时或结果未知；远端业务状态仍需独立核验。"
    return {"execution_id": execution_id, "grading_batch_id": batch_id, "trace_id": trace_id,
        "headline": headline, "items": items, "scope": "固定执行和评分批次的确定性事实摘要，不是根因结论；差异最多展示 20 项。"}
