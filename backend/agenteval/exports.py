import csv
import hashlib
import html
import io
import json

SENSITIVE = {"api_key", "apikey", "password", "authorization", "access_token", "secret", "token"}


def redact(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in SENSITIVE else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value


def safe_cell(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def render(bundle, format):
    bundle = redact(bundle)
    payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(payload.encode()).hexdigest()
    if format == "json":
        return json.dumps({"content_hash": checksum, **bundle}, ensure_ascii=False, indent=2), "application/json"
    report = bundle["report"]
    rows = report.get("trials", [])
    if format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["case_key", "repetition", "verdict", "execution_status", "execution_id", "reasons", "grading_batch_id", "content_hash"])
        for row in rows:
            writer.writerow([safe_cell(v) for v in [row["case_key"], row["repetition_index"] + 1, row["verdict"],
                row["execution_status"], row["execution_id"], ", ".join(row["reasons"]), report["grading_batch_id"], checksum]])
        return "\ufeff" + output.getvalue(), "text/csv; charset=utf-8"
    def esc(value):
        return html.escape(str(value))
    table = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in [r["case_key"], r["repetition_index"] + 1, r["verdict"], r["execution_status"], ", ".join(r["reasons"])]) + "</tr>" for r in rows)
    metrics = {k: report[k] for k in ("counts", "success_rate", "decision_coverage", "scoring_coverage", "latency_ms")}
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>AgentEval report</title>
<style>body{{font:14px system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#192c37}}table{{width:100%;border-collapse:collapse}}th,td{{padding:12px;border:1px solid #d5dfe5;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f7;padding:18px}}small{{color:#5a6c78}}</style>
<h1>AgentEval 评测报告</h1><p>Run: {esc(report['run_id'])}</p><p>固定评分批次：{esc(report['grading_batch_id'])}</p><pre>{esc(json.dumps(metrics,ensure_ascii=False,indent=2))}</pre>
<table><thead><tr><th>用例</th><th>重复试验</th><th>判定</th><th>执行</th><th>依据</th></tr></thead><tbody>{table}</tbody></table>
<h2>配置与证据</h2><details><summary>查看完整导出数据</summary><pre>{esc(json.dumps(bundle,ensure_ascii=False,indent=2))}</pre></details>
<p><small>SHA-256: {checksum} · 敏感字段按名称脱敏；业务文本仍需按项目策略审核。</small></p></html>''', "text/html; charset=utf-8"
