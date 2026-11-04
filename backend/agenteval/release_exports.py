"""Offline, escaped release report for review outside the console."""
import html
import json


def render_release(value):
    esc = lambda item: html.escape(str(item))
    snapshot = value["snapshot"]
    comparison = snapshot["comparison"]
    label = {"ready": "可发布", "blocked": "阻断发布", "insufficient": "证据不足"}[value["status"]]
    def percent(number):
        return "不可用" if number is None else f"{number * 100:.1f}%"
    versions = snapshot["versions"]
    version_text = " → ".join(f"{versions[side]['agent_name']} v{versions[side]['version']}" for side in ("baseline", "candidate"))
    metrics = [("基线通过率", percent(comparison["baseline"]["success_rate"])),
               ("候选通过率", percent(comparison["candidate"]["success_rate"])),
               ("发布证据完整", "是" if snapshot["evidence_complete"] else "否"),
               ("开放阻断项", len(snapshot["blocking_issues"]))]
    cards = "".join(f"<div><small>{esc(key)}</small><strong>{esc(item)}</strong></div>" for key, item in metrics)
    reason_labels = {"BENCHMARK_SCOPE_MISMATCH": "基线与候选的 Benchmark 固定版本不同", "AGENT_SCOPE_MISMATCH": "基线和候选不属于同一 Agent 身份，不能据此做版本发布判断", "ENGINE_REASSESSMENT_REQUIRED": "证据来自较旧引擎，需重新评分并建立当前版本比较", "UNRESOLVED_BLOCKING_ISSUES": "同范围阻断问题尚未完成复测确认", "NEWER_GRADING_BATCH_EXISTS": "候选存在更新的评分批次，需要重新比较", "RELEASE_EVIDENCE_INCOMPLETE": "固定批次判定或评分证据不完整", "SUCCESS_RATE_THRESHOLD": "成功率或允许下降幅度不满足门禁", "HARD_CONSTRAINT_OR_CORE_CASE_FAILURE": "核心用例或硬约束失败", "EXPLORATORY_OR_INCOMPATIBLE": "探索比较或评测条件不兼容", "INCOMPLETE_OR_VARIABLE_EVIDENCE": "证据不完整或重复试验波动"}
    reasons = "".join(f"<li>{esc(reason_labels.get(code, code))}</li>" for code in snapshot["reasons"]) or "<li>固定比较门禁通过，证据完整，未发现同范围开放阻断项。</li>"
    issues = "".join(f"<tr><td>{esc(i['title'])}</td><td>{esc(i['status'])}</td><td>{esc(i['revision'])}</td></tr>" for i in snapshot["blocking_issues"])
    scope = (comparison["candidate"].get("specialty_metrics") or {}).get("state", {}).get("validation_scope")
    coverage_note = "当前方案包含独立环境状态评分。" if scope == "independent_state" else "当前方案未完整独立核验业务状态；结论仅覆盖已配置的输出与过程规则。"
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>AgentEval 发布评估 · {esc(label)}</title>
<style>body{{font:15px system-ui,sans-serif;color:#20323e;max-width:1040px;margin:40px auto;padding:0 24px}}h1{{font-size:30px}}.state{{display:inline-block;padding:8px 16px;border:1px solid #8da5ac;border-radius:8px;background:#ecf5f3}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}}.metrics div{{border:1px solid #d7e1e5;padding:18px;border-radius:10px}}small{{color:#5b6e79}}strong{{display:block;font-size:25px;margin-top:12px}}p,li{{line-height:1.8}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f7;padding:18px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;border-bottom:1px solid #d7e1e5;padding:12px}}footer{{margin-top:32px;overflow-wrap:anywhere}}@media(max-width:640px){{.metrics{{grid-template-columns:repeat(2,1fr)}}}}</style>
<h1>版本发布评估</h1><p>{esc(version_text)}</p><span class="state">{esc(label)}</span><div class="metrics">{cards}</div>
<h2>建议与依据</h2><ul>{reasons}</ul><p>{esc(snapshot['note'] or '未附加人工评估说明。')}</p><p>{esc(coverage_note)}</p><p>{esc(snapshot['scope'])}</p>
<h2>生成时的开放阻断项</h2>{'<table><tr><th>问题</th><th>状态</th><th>修订号</th></tr>'+issues+'</table>' if issues else '<p>无。</p>'}
<h2>固定证据</h2><p>基线批次：{esc(comparison['baseline_batch_id'])}<br>候选批次：{esc(comparison['candidate_batch_id'])}<br>生成时间：{esc(value['created_at'])}</p>
<p>后续重评或问题更新不会改写此快照。修复后需重新比较并生成发布评估。</p><details><summary>查看完整决策证据</summary><pre>{esc(json.dumps(value,ensure_ascii=False,indent=2))}</pre></details>
<footer><small>报告 ID：{esc(value['id'])}<br>内容 SHA-256：{esc(value['content_hash'])}</small></footer></html>'''
