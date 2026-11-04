import { useEffect, useState } from 'react';
import type { API } from './api';
import type { Run } from './types';
import type { Action } from './Pages';
import { Badge, date, Empty, ErrorBox, Heading, Icon, Pager, short } from './ui';
import { useAccess } from './Auth';
import { QualityConclusion } from './QualityMetrics';

function read() { return new URLSearchParams(window.location.hash.split('?')[1] || ''); }
export function RunsPage({ api, action, projectId }: { api: API; action: (kind: Action) => void; projectId: string }) {
  const { write } = useAccess();
  const [query, setQuery] = useState(read), [revision, setRevision] = useState(0);
  const [items, setItems] = useState<Run[]>([]), [total, setTotal] = useState(0), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const page = Math.max(0, Number(query.get('page')) || 0);
  useEffect(() => { const changed = () => setQuery(read()); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed); }, []);
  const key = query.toString();
  useEffect(() => {
    let alive = true, poll: ReturnType<typeof setTimeout>;
    setLoading(true); setError(''); setItems([]); setTotal(0);
    async function load() {
      const params = new URLSearchParams(key); params.delete('page'); params.set('project_id', projectId); params.set('offset', String(page * 10)); params.set('limit', '10');
      try {
        const result = await api.get<{ items: Run[]; total: number }>(`evaluations?${params}`);
        if (!alive) return;
        setItems(result.items); setTotal(result.total); setError(''); setLoading(false);
        if (result.items.some(r => !['completed', 'failed', 'cancelled'].includes(r.status) || r.quality_summary?.quality_status === 'pending')) poll = setTimeout(() => void load(), 2500);
      } catch (e) { if (alive) { setError((e as Error).message); setLoading(false); } }
    }
    const debounce = setTimeout(() => void load(), 200);
    return () => { alive = false; clearTimeout(debounce); clearTimeout(poll); };
  }, [api, projectId, key, page, revision]);
  function change(field: string, value: string) {
    const next = new URLSearchParams(query); next.set('project_id', projectId);
    if (value) next.set(field, value); else next.delete(field);
    if (field !== 'page') next.delete('page');
    setQuery(next);
    window.location.hash = `/runs?${next}`;
  }
  function link(run: Run, bad = false) {
    const back = new URLSearchParams(query); back.set('project_id', projectId);
    const params = new URLSearchParams({ return_to: `/runs?${back}`, project_id: projectId });
    if (bad) params.set('filter', 'bad');
    if (run.quality_summary?.grading_batch_id) params.set('grading_batch_id', run.quality_summary.grading_batch_id);
    return `#/runs/${run.id}?${params}`;
  }
  return <><Heading title="评测任务" description="执行状态与固定评分批次的质量结论分别展示。" action={<button className="button primary" disabled={!write} onClick={() => action('run')}><Icon name="plus"/>创建评测</button>}/>
    <div className="task-filters"><label className="search-input"><Icon name="search"/><input aria-label="搜索任务" maxLength={200} value={query.get('q') || ''} onChange={e => change('q', e.target.value)} placeholder="搜索全部任务 ID、Agent 或 Benchmark"/></label>
      <select aria-label="筛选状态" value={query.get('status') || ''} onChange={e => change('status', e.target.value)}><option value="">全部执行状态</option>{[['queued', '排队中'], ['running', '执行中'], ['evaluating', '评分中'], ['completed', '已完成'], ['cancelled', '已取消'], ['failed', '执行失败']].map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select>
      <select aria-label="筛选质量结论" value={query.get('quality') || ''} onChange={e => change('quality', e.target.value)}><option value="">全部质量结论</option><option value="passed">通过</option><option value="failed">不通过</option><option value="insufficient">证据不足</option><option value="pending">评分未结束</option></select>
      <label className="field"><span>Agent 版本</span><input maxLength={120} value={query.get('version') || ''} onChange={e => change('version', e.target.value)} placeholder="精确版本名"/></label>
      <label className="field"><span>起始日期（UTC）</span><input type="date" value={query.get('created_after')?.slice(0,10) || ''} onChange={e => change('created_after', e.target.value ? e.target.value + 'T00:00:00Z' : '')}/></label>
      <label className="field"><span>结束日期（UTC）</span><input type="date" value={query.get('created_before')?.slice(0,10) || ''} onChange={e => change('created_before', e.target.value ? e.target.value + 'T23:59:59.999999Z' : '')}/></label>
      <select aria-label="任务排序" value={query.get('sort') || 'newest'} onChange={e => change('sort', e.target.value)}><option value="newest">最新创建优先</option><option value="oldest">最早创建优先</option></select><button className="button secondary" onClick={() => { window.location.hash = `/runs?project_id=${projectId}`; }}>清除筛选</button><button className="icon-button" aria-label="刷新任务列表" title="刷新任务列表" onClick={() => setRevision(v => v + 1)}><Icon name="refresh"/></button></div>
    <ErrorBox message={error}/><section className="panel" aria-busy={loading}>{loading ? <p className="loading-message" role="status">正在查询任务与固定批次摘要…</p> : error ? <div className="form-body"><p>查询失败，已保留筛选条件。</p><button className="button secondary" onClick={() => setRevision(v => v + 1)}>重试查询</button></div> : items.length ? <div className="table-scroll"><table className="task-table"><thead><tr><th>评测任务 / Agent</th><th>Benchmark</th><th>执行状态</th><th>质量结论</th><th>耗时 / 发起人</th><th>创建 / 结果更新</th><th/></tr></thead><tbody>{items.map(r => { const s = r.quality_summary; return <tr key={r.id}><td><a className="table-link" href={link(r)} title={`${r.agent_name} · v${r.agent_version}`}>{r.agent_name} · v{r.agent_version}</a><small className="secondary-line mono">RUN-{short(r.id)}</small></td><td>{r.benchmark_name} · v{r.benchmark_version}</td><td><Badge status={r.status}/></td><td><QualityConclusion value={s}/><small className="secondary-line">{s?.counts.passed ?? '—'} 通过 · {s?.counts.failed ? <a href={link(r, true)}>{s.counts.failed} 失败 →</a> : '0 失败'} · {(s?.counts.unknown || 0) + (s?.unassessed_trials || 0)} 待判定</small><small className="secondary-line">{s?.counts.excluded || 0} 排除 · 第 {s?.revision == null ? '—' : s.revision + 1} 批 / {short(s?.grading_batch_id || '')}</small></td><td>{s?.duration_ms == null ? '耗时未记录' : `${Math.round(s.duration_ms)} ms 调用累计`}<small className="secondary-line">{s?.initiator.display_name || '未记录个人身份'}</small></td><td>{date(r.created_at)}<small className="secondary-line">结果 {s?.updated_at ? date(s.updated_at) : '尚未记录'}</small></td><td><a href={link(r)} aria-label={`查看任务 ${short(r.id)}`} title="查看任务"><Icon name="chevron"/></a></td></tr>; })}</tbody></table></div> : <Empty title="没有匹配的任务" description="搜索覆盖当前项目全部任务；可清除筛选或创建新评测。"/>}</section><Pager page={page} size={10} total={total} onChange={v => change('page', String(v))}/><p className="form-note">耗时为已记录执行尝试的调用累计，包含失败尝试，不代表墙钟耗时；发起人由服务端身份记录，历史共享/未知身份保留占位。</p></>;
}
