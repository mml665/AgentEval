import type { Comparison, ComparisonSide } from './types';
import { date, short } from './ui';

function label(side?: ComparisonSide | null) { return side ? `${side.agent_name} · v${side.agent_version}` : '历史对象信息缺失'; }
export function comparisonLabel(c: Comparison) {
  return `${label(c.context?.baseline)} → ${label(c.context?.candidate)} · ${c.context?.candidate?.benchmark_name || 'Benchmark'} v${c.context?.candidate?.benchmark_version || '—'} · 批次 ${short(c.report.baseline_batch_id)}/${short(c.report.candidate_batch_id)} · CMP-${short(c.id)}`;
}
export function ComparisonObjects({ value }: { value: Comparison }) {
  return <div className="comparison-objects">{(['baseline', 'candidate'] as const).map(side => { const v = value.context?.[side]; return <div key={side}><strong>{side === 'baseline' ? '基线' : '候选'}：{label(v)}</strong><small>{v?.benchmark_name || 'Benchmark 未记录'} · v{v?.benchmark_version || '—'}</small><small>运行 {v ? date(v.run_created_at) : '未记录'} · {short(value.report[`${side}_batch_id`])} / 第 {v ? v.batch_revision + 1 : '—'} 批</small></div>; })}</div>;
}
