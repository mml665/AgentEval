import { useEffect, useState } from 'react';
import type { API } from './api';
import { Badge, Code, Empty, ErrorBox } from './ui';

interface Span { span_id: string; parent_span_id: string | null; name: string; kind: string; start_ns: number; end_ns: number; duration_ms: number; status: string; attributes: Record<string, unknown>; events: { name: string; time_ns: number; attributes: Record<string, unknown> }[] }
interface Trace { completeness: string; spans: Span[]; trace_id: string }

export function TraceView({ api, executionId }: { api: API; executionId: string }) {
  const [trace, setTrace] = useState<Trace | null>(null), [selected, setSelected] = useState(''), [error, setError] = useState('');
  useEffect(() => { let alive = true; void api.get<Trace>(`executions/${executionId}/trace`).then(t => { if (alive) { setTrace(t); setSelected(t.spans[0]?.span_id || ''); } }).catch(e => { if (alive) setError((e as Error).message); }); return () => { alive = false; }; }, [api, executionId]);
  if (error) return <ErrorBox message={error}/>;
  if (!trace?.spans.length) return <Empty title="尚无结构化 Trace" description="旧版本执行或缺少观测的 Agent 可能仅有输出。"/>;
  const map = new Map(trace.spans.map(s => [s.span_id, s]));
  function depth(span: Span) { let result = 0, parent = span.parent_span_id; const seen = new Set<string>(); while (parent && map.has(parent) && !seen.has(parent)) { seen.add(parent); result++; parent = map.get(parent)!.parent_span_id; } return result; }
  const rows: Span[] = [];
  function children(parent: string | null) { for (const s of trace!.spans.filter(x => x.parent_span_id === parent)) { rows.push(s); children(s.span_id); } }
  children(null);
  const start = Math.min(...trace.spans.map(s => s.start_ns)), end = Math.max(...trace.spans.map(s => s.end_ns)), duration = Math.max(1, end - start);
  const detail = map.get(selected);
  const complete = trace.completeness === 'instrumented';
  return <section className="trace-view"><div className="trace-title"><h3>执行链路与时间线</h3><span className="tag">{complete ? '已采集埋点范围' : '内部轨迹不完整'} · {trace.spans.length} spans</span></div>{!complete && <p className="muted">当前只包含可观测范围，不能据此推断缺失的内部步骤。</p>}<div className="trace-tree" aria-label="执行链路树">{rows.map(s => <button key={s.span_id} className={`span-row ${selected === s.span_id ? 'selected' : ''}`} onClick={() => setSelected(s.span_id)}><span className="span-name" style={{ paddingLeft: `${depth(s) * 14}px` }}><span className={`span-dot kind-${s.kind}`}/>{s.name}<small>{s.kind}</small></span><span className="span-track"><i className={`kind-${s.kind}`} style={{ left: `${(s.start_ns - start) / duration * 100}%`, width: `${Math.max(.5, (s.end_ns - s.start_ns) / duration * 100)}%` }}/></span><span className="mono">{s.attributes['span.open'] ? '未结束' : `${s.duration_ms.toFixed(2)} ms`}</span></button>)}</div>{detail && <div className="span-detail"><div className="trace-title"><strong>{detail.name}</strong><Badge status={detail.status === 'error' ? 'failed' : detail.status === 'ok' ? 'completed' : 'unknown'}/></div><small className="muted mono">SPAN {detail.span_id} · PARENT {detail.parent_span_id || 'root'}</small><Code value={detail.attributes}/>{detail.events.map((event, index) => <div className="span-event" key={index}><strong>{event.name}</strong><small className="muted mono"> +{((event.time_ns - start) / 1e6).toFixed(2)} ms</small><Code value={event.attributes}/></div>)}</div>}</section>;
}
