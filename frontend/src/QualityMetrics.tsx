import { useState } from 'react';
import type { API } from './api';
import type { SpecialtyMetrics } from './types';
import { Code, ErrorBox, pct } from './ui';

const grainNames: Record<string, string> = { e2e: '端到端结果', trajectory: '执行轨迹', tool: '单步工具', component: '组件评测' };
export function QualityMetrics({ value }: { value?: SpecialtyMetrics }) {
  if (!value) return null;
  const scope = value.state.validation_scope;
  return <section className="panel"><div className="panel-heading"><div><h2>专项能力与证据覆盖</h2><p>{scope === 'output_only' ? '仅验证输出与声明的过程观测；未独立核验业务环境状态。' : scope === 'independent_state' ? '已使用独立环境状态快照评分；快照在环境重置前保存。' : '已配置独立状态验证，当前证据尚不完整，请结合覆盖率判断。'}</p></div></div>
    <div className="table-scroll"><table><thead><tr><th>评测粒度 / 规则</th><th>质量维度</th><th>机制</th><th>平均得分</th><th>通过率</th><th>证据覆盖</th></tr></thead><tbody>{Object.entries(value.granularities).flatMap(([grain, group]) => group.evaluators.length ? group.evaluators.map(item => <tr key={item.instance_id}><td>{grainNames[grain]}<small className="secondary-line mono">{item.instance_id} · {item.evaluator_id}</small></td><td>{item.metadata.quality_dimension}</td><td>{item.metadata.scoring_method}</td><td>{item.mean_score?.toFixed(3) ?? '—'}</td><td>{pct(item.pass_rate)}</td><td>{pct(item.coverage)} · {item.scored}/{item.expected}</td></tr>) : [<tr key={grain}><td>{grainNames[grain]}</td><td colSpan={5} className="muted">当前方案未配置此粒度评分</td></tr>])}</tbody></table></div>
    <dl className="protocol-list"><div><dt>环境状态成功率 / 覆盖率</dt><dd>{pct(value.state.success_rate)} / {pct(value.state.coverage)}</dd></div><div><dt>工具调用错误率</dt><dd>{pct(value.tool.error_rate)} · {value.tool.samples} 次有状态证据的调用</dd></div><div><dt>循环率 / 恢复率</dt><dd>{pct(value.trajectory.loop_rate)} / {pct(value.trajectory.recovery_rate)}</dd></div><div><dt>超时率 / 重复试验全部成功率</dt><dd>{pct(value.reliability.timeout_rate)} / {pct(value.reliability.repeated_run_success)}</dd></div><div><dt>Agent Tokens / Task</dt><dd>{value.usage.agent_tokens_per_task?.toFixed(1) ?? '—'} · 覆盖 {pct(value.usage.agent_token_coverage)}</dd></div></dl>
    {value.state.reset_failures > 0 && <ErrorBox message={`有 ${value.state.reset_failures} 次环境重置失败，请先排查环境后再作发布判断。`}/>}</section>;
}

export function CalibrationPanel({ api, runId, batchId, instances }: { api: API; runId: string; batchId: string; instances: string[] }) {
  const [source, setSource] = useState(''), [instance, setInstance] = useState(instances[0] || ''), [labels, setLabels] = useState('[]');
  const [report, setReport] = useState<unknown>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  if (!instances.length) return null;
  async function compare() {
    setBusy(true); setError(''); setReport(null);
    try { setReport(await api.post(`evaluations/${runId}/calibration`, { grading_batch_id: batchId, instance_id: instance, annotation_source: source, labels: JSON.parse(labels) })); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel"><div className="panel-heading"><div><h2>Judge 人工标签校准</h2><p>比较当前固定批次，不重新调用模型。标签只用于分析，不改写历史评分。</p></div></div><div className="form-body"><label className="field"><span>裁判评分项</span><select value={instance} onChange={e => { setInstance(e.target.value); setReport(null); }}>{instances.map(id => <option key={id}>{id}</option>)}</select></label><label className="field"><span>标注来源</span><input value={source} maxLength={200} onChange={e => { setSource(e.target.value); setReport(null); }} placeholder="例如：评测组双人复核 / 2026-11-03"/></label><label className="field"><span>人工标签 JSON</span><textarea className="code-input" rows={6} value={labels} onChange={e => { setLabels(e.target.value); setReport(null); }} placeholder={'[{"case_key":"case-001","label":"failed","rationale":"事实错误"}]'}/><small>label 支持 passed、failed、pending_review；需填写 case_key 和 rationale。</small></label><button className="button secondary" disabled={busy || !source.trim()} onClick={() => void compare()}>{busy ? '分析中…' : '生成校准分析'}</button><ErrorBox message={error}/>{report != null && <Code value={report}/>}</div></section>;
}
