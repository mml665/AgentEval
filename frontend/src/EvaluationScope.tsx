import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import type { API } from './api';
import type { Run } from './types';
import { ErrorBox } from './ui';

export interface ScopeRun extends Run { agent_id: string; benchmark_id: string; agent_label: string; benchmark_label: string }
export interface Selection { agent_id: string; candidate_version_id: string; baseline_version_id: string; benchmark_version_id: string }
interface Value { runs: ScopeRun[]; selection: Selection | null; query: string; loading: boolean; error: string; select: (field: keyof Selection, value: string) => void; reload: () => Promise<void> }
const Context = createContext<Value | null>(null);
function initial(runs: ScopeRun[], agent?: string, version?: string, benchmark?: string): Selection | null {
  const first = runs.find(r => (!agent || r.agent_id === agent) && (!version || r.agent_version_id === version) && (!benchmark || r.benchmark_version_id === benchmark));
  if (!first) return null;
  const baseline = runs.find(r => r.agent_id === first.agent_id && r.benchmark_version_id === first.benchmark_version_id && r.agent_version_id !== first.agent_version_id);
  return { agent_id: first.agent_id, candidate_version_id: first.agent_version_id, baseline_version_id: baseline?.agent_version_id || '', benchmark_version_id: first.benchmark_version_id };
}
export function ScopeProvider({ api, projectId, children }: { api: API; projectId: string; children: ReactNode }) {
  const [runs, setRuns] = useState<ScopeRun[]>([]), [selection, setSelection] = useState<Selection | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const current = useRef(selection); current.current = selection;
  const generation = useRef(0);
  const pendingRun = useRef('');
  const reload = useCallback(async () => {
    const epoch = ++generation.current;
    if (!projectId) { setRuns([]); setSelection(null); setLoading(false); return; }
    setLoading(true);
    try {
      const rows = await api.all<ScopeRun>(`quality/runs?project_id=${projectId}`);
      if (epoch !== generation.current) return;
      setRuns(rows); setError('');
      const requested = rows.find(r => r.id === pendingRun.current);
      if (requested) { pendingRun.current = ''; setSelection(initial(rows, requested.agent_id, requested.agent_version_id, requested.benchmark_version_id)); return; }
      const old = current.current;
      setSelection(old && rows.some(r => r.agent_id === old.agent_id && r.agent_version_id === old.candidate_version_id && r.benchmark_version_id === old.benchmark_version_id) ? old : initial(rows));
    } catch (e) { if (epoch === generation.current) setError((e as Error).message); }
    finally { if (epoch === generation.current) setLoading(false); }
  }, [api, projectId]);
  useEffect(() => { current.current = null; setSelection(null); setRuns([]); void reload(); return () => { generation.current++; }; }, [reload]);
  useEffect(() => { const refresh = () => void reload(); window.addEventListener('hashchange', refresh); return () => window.removeEventListener('hashchange', refresh); }, [reload]);
  useEffect(() => { const selectRun = (event: Event) => { pendingRun.current = (event as CustomEvent<{ run_id: string }>).detail.run_id; void reload(); }; window.addEventListener('agenteval-scope-run', selectRun); const timer = setInterval(() => { if (pendingRun.current) void reload(); }, 2500); return () => { window.removeEventListener('agenteval-scope-run', selectRun); clearInterval(timer); }; }, [reload]);
  const query = selection ? new URLSearchParams(Object.entries(selection).filter(([, value]) => Boolean(value))).toString() : '';
  function select(field: keyof Selection, value: string) {
    if (field === 'agent_id') setSelection(initial(runs, value));
    else if (field === 'candidate_version_id') setSelection(initial(runs, selection?.agent_id, value));
    else if (field === 'benchmark_version_id') setSelection(initial(runs, selection?.agent_id, selection?.candidate_version_id, value));
    else setSelection(s => s ? { ...s, baseline_version_id: value } : s);
  }
  return <Context.Provider value={{ runs, selection, query, loading, error, select, reload }}>{children}</Context.Provider>;
}
export function useEvaluationScope() {
  const value = useContext(Context);
  if (!value) throw new Error('Evaluation scope provider is missing');
  return value;
}
export function ScopeSelectors() {
  const { runs, selection, select, loading, error, reload } = useEvaluationScope();
  const unique = (items: ScopeRun[], key: keyof ScopeRun) => items.filter((r, i) => items.findIndex(v => v[key] === r[key]) === i);
  const agents = unique(runs, 'agent_id');
  const versions = unique(runs.filter(r => r.agent_id === selection?.agent_id), 'agent_version_id');
  const benchmarks = unique(runs.filter(r => r.agent_id === selection?.agent_id && r.agent_version_id === selection?.candidate_version_id), 'benchmark_version_id');
  const baseline = unique(runs.filter(r => r.agent_id === selection?.agent_id && r.benchmark_version_id === selection?.benchmark_version_id), 'agent_version_id');
  return <section className="panel scope-panel"><ErrorBox message={error}/><div className="scope-selectors">
    <label className="field"><span>Agent 评测身份</span><select aria-label="Agent 评测身份" value={selection?.agent_id || ''} disabled={!runs.length} onChange={e => select('agent_id', e.target.value)}>{!agents.length && <option value="">暂无已结束评测</option>}{agents.map(r => <option key={r.agent_id} value={r.agent_id}>{r.agent_label.split(' · v')[0]}</option>)}</select></label>
    <label className="field"><span>候选版本</span><select aria-label="候选版本" value={selection?.candidate_version_id || ''} disabled={!selection} onChange={e => select('candidate_version_id', e.target.value)}>{versions.map(r => <option key={r.agent_version_id} value={r.agent_version_id}>{r.agent_label}</option>)}</select></label>
    <label className="field"><span>基线版本</span><select aria-label="基线版本" value={selection?.baseline_version_id || ''} disabled={!selection} onChange={e => select('baseline_version_id', e.target.value)}><option value="">未选择基线</option>{baseline.map(r => <option key={r.agent_version_id} value={r.agent_version_id}>{r.agent_label}</option>)}</select></label>
    <label className="field"><span>Benchmark 固定版本</span><select aria-label="Benchmark 固定版本" value={selection?.benchmark_version_id || ''} disabled={!selection} onChange={e => select('benchmark_version_id', e.target.value)}>{benchmarks.map(r => <option key={r.benchmark_version_id} value={r.benchmark_version_id}>{r.benchmark_label} · {r.benchmark_version_id.slice(0, 8)}</option>)}</select></label>
    <button className="button secondary" disabled={loading} onClick={() => void reload()}>刷新范围</button>
  </div><p className="form-note">质量指标、待确认异常、正式缺陷和发布记录均绑定此范围。全项目统计请查看“项目总览”。</p></section>;
}
