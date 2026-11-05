import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import type { API } from './api';
import type { Batch, Run } from './types';
import { useAccess } from './Auth';
import { ErrorBox, short } from './ui';

export interface ScopeRun extends Run { agent_id: string; benchmark_id: string; agent_label: string; benchmark_label: string }
export interface Selection { agent_id: string; candidate_version_id: string; baseline_version_id: string; benchmark_version_id: string; candidate_batch_id: string; baseline_batch_id: string }
interface Baseline { id: string; agent_id: string; benchmark_version_id: string; grading_batch_id: string; revision: number; run: Run }
interface Value { runs: ScopeRun[]; batches: Batch[]; selection: Selection | null; query: string; loading: boolean; error: string; select: (field: keyof Selection, value: string) => void; reload: () => Promise<void>; saveBaseline: () => Promise<void>; swap: () => void }
const Context = createContext<Value | null>(null);
const keys = ['agent_id','candidate_version_id','baseline_version_id','benchmark_version_id','candidate_batch_id','baseline_batch_id'] as const;
const scoped = () => /^#\/(overview|issues|releases|regressions)(\/|\?|$)/.test(window.location.hash) || /^#\/runs\//.test(window.location.hash);
export function ScopeProvider({ api, projectId, children }: { api: API; projectId: string; children: ReactNode }) {
  const [runs,setRuns] = useState<ScopeRun[]>([]), [batches,setBatches] = useState<Batch[]>([]), [baselines,setBaselines] = useState<Baseline[]>([]), [selection,setSelection] = useState<Selection | null>(null), [loading,setLoading] = useState(false), [error,setError] = useState('');
  const current = useRef(selection); current.current = selection;
  const generation = useRef(0), pendingRun = useRef(window.location.hash.match(/^#\/runs\/([^?]+)/)?.[1] || '');
  const pendingComparison = useRef<{baseline_run_id:string;candidate_run_id:string;baseline_batch_id:string;candidate_batch_id:string;path:string} | null>(null);
  function initial(rows: ScopeRun[], bs: Batch[], saved: Baseline[], agent?: string, version?: string, benchmark?: string): Selection | null {
    const first = rows.find(r => (!agent || r.agent_id === agent) && (!version || r.agent_version_id === version) && (!benchmark || r.benchmark_version_id === benchmark));
    if (!first) return null;
    const base = saved.find(b => b.agent_id === first.agent_id && b.benchmark_version_id === first.benchmark_version_id);
    return { agent_id:first.agent_id,candidate_version_id:first.agent_version_id,benchmark_version_id:first.benchmark_version_id,candidate_batch_id:bs.find(b => b.run_id === first.id)?.id || '',baseline_version_id:base?.run.agent_version_id || '',baseline_batch_id:base?.grading_batch_id || '' };
  }
  function publish(value: Selection | null, push = false) {
    const requestedProject=new URLSearchParams(window.location.hash.split('?')[1] || '').get('project_id');
    if (requestedProject && requestedProject !== projectId) return;
    current.current = value; setSelection(value);
    if (!value || !scoped()) return;
    const [path,query=''] = window.location.hash.split('?'), params = new URLSearchParams(query);
    keys.forEach(k => params.set(k,value[k])); params.set('project_id',projectId); params.set('scope','1');
    const next = `${path}?${params}`;
    if (push) { if (next !== window.location.hash) window.location.hash = next; }
    else history.replaceState(null,'',next);
  }
  const reload = useCallback(async () => {
    const epoch = ++generation.current;
    if (!projectId) { setRuns([]); setSelection(null); return; } setLoading(true);
    try {
      const [rows,bs,bases] = await Promise.all([api.all<ScopeRun>(`quality/runs?project_id=${projectId}`),api.all<Batch>(`quality/batches?project_id=${projectId}`),api.get<{ items:Baseline[] }>(`quality/baselines/${projectId}`)]);
      if (epoch !== generation.current) return; setRuns(rows); setBatches(bs); setBaselines(bases.items); setError('');
      const params = new URLSearchParams(window.location.hash.split('?')[1] || '');
      if (pendingComparison.current && pendingComparison.current.path !== window.location.hash.split('?')[0]) pendingComparison.current=null;
      if (pendingComparison.current) {
        const detail=pendingComparison.current, candidate=rows.find(r => r.id === detail.candidate_run_id), baseline=rows.find(r => r.id === detail.baseline_run_id);
        if (candidate) { const value=initial(rows,bs,bases.items,candidate.agent_id,candidate.agent_version_id,candidate.benchmark_version_id)!; value.candidate_batch_id=detail.candidate_batch_id;
          if (baseline?.agent_id === candidate.agent_id && baseline.benchmark_version_id === candidate.benchmark_version_id) { value.baseline_version_id=baseline.agent_version_id; value.baseline_batch_id=detail.baseline_batch_id; }
          else { value.baseline_version_id=''; value.baseline_batch_id=''; }
          pendingComparison.current=null; publish(value); return;
        }
      }
      const wanted = rows.find(r => r.id === pendingRun.current);
      if (wanted) { const value=initial(rows,bs,bases.items,wanted.agent_id,wanted.agent_version_id,wanted.benchmark_version_id)!; const explicit=params.get('grading_batch_id'); if (explicit) { if (!bs.some(b => b.id === explicit && b.run_id === wanted.id)) throw new Error('链接中的评分批次尚未完成或不属于此任务，未选择其他批次。'); value.candidate_batch_id=explicit; } pendingRun.current=''; publish(value); return; }
      let value: Selection | null;
      if (params.has('scope') || params.has('agent_id')) {
        value = Object.fromEntries(keys.map(k => [k,params.get(k) || ''])) as unknown as Selection;
        const matches = rows.filter(r => r.agent_id === value!.agent_id && r.agent_version_id === value!.candidate_version_id && r.benchmark_version_id === value!.benchmark_version_id);
        const validBatch = (id: string, version: string) => !id || bs.some(b => b.id === id && rows.some(r => r.id === b.run_id && r.agent_id === value!.agent_id && r.agent_version_id === version && r.benchmark_version_id === value!.benchmark_version_id));
        if (!matches.length || !validBatch(value.candidate_batch_id,value.candidate_version_id) || !validBatch(value.baseline_batch_id,value.baseline_version_id) || value.baseline_version_id && !rows.some(r => r.agent_id === value!.agent_id && r.agent_version_id === value!.baseline_version_id && r.benchmark_version_id === value!.benchmark_version_id)) throw new Error('链接中的评测范围或批次已失效，未自动切换到其他对象。请选择有效范围。');
        if (!value.candidate_batch_id) value.candidate_batch_id = bs.find(b => b.run_id === matches[0].id)?.id || '';
      } else value = current.current && rows.some(r => r.agent_id === current.current!.agent_id && r.agent_version_id === current.current!.candidate_version_id && r.benchmark_version_id === current.current!.benchmark_version_id) ? current.current : initial(rows,bs,bases.items);
      publish(value);
    } catch (e) { if (epoch === generation.current) { current.current = null; setSelection(null); setError((e as Error).message); } }
    finally { if (epoch === generation.current) setLoading(false); }
  }, [api,projectId]);
  useEffect(() => { if (pendingRun.current && !window.location.hash.startsWith('#/runs/'+pendingRun.current)) pendingRun.current=''; current.current = null; setSelection(null); setRuns([]); void reload(); return () => { generation.current++; }; }, [reload]);
  useEffect(() => { const compare = (event:Event) => { pendingComparison.current={...(event as CustomEvent).detail,path:window.location.hash.split('?')[0]}; void reload(); }; window.addEventListener('agenteval-scope-comparison',compare); const refresh = () => { if (scoped()) void reload(); }; const choose = (event: Event) => { pendingRun.current = (event as CustomEvent<{ run_id:string }>).detail.run_id; void reload(); }; window.addEventListener('hashchange',refresh); window.addEventListener('agenteval-scope-run',choose); const timer = setInterval(() => { if (pendingRun.current) void reload(); },2500); return () => { window.removeEventListener('agenteval-scope-comparison',compare); window.removeEventListener('hashchange',refresh); window.removeEventListener('agenteval-scope-run',choose); clearInterval(timer); }; }, [reload]);
  function select(field: keyof Selection, value: string) {
    setError(''); let next = selection;
    if (field === 'agent_id') next = initial(runs,batches,baselines,value);
    else if (field === 'candidate_version_id') next = initial(runs,batches,baselines,selection?.agent_id,value);
    else if (field === 'benchmark_version_id') next = initial(runs,batches,baselines,selection?.agent_id,selection?.candidate_version_id,value);
    else if (next) { next = { ...next,[field]:value }; if (field === 'baseline_version_id') next.baseline_batch_id = batches.find(b => runs.some(r => r.id === b.run_id && r.agent_id === next!.agent_id && r.agent_version_id === value && r.benchmark_version_id === next!.benchmark_version_id))?.id || ''; }
    publish(next,true);
  }
  async function saveBaseline() { if (!selection?.baseline_batch_id) return; const previous = baselines.find(b => b.agent_id === selection.agent_id && b.benchmark_version_id === selection.benchmark_version_id); try { await api.post(`quality/baselines/${projectId}`,{ agent_id:selection.agent_id,benchmark_version_id:selection.benchmark_version_id,grading_batch_id:selection.baseline_batch_id,expected_revision:previous?.revision || 0 }); await reload(); } catch (e) { setError((e as Error).message); } }
  function swap() { if (selection?.baseline_version_id) publish({ ...selection,candidate_version_id:selection.baseline_version_id,candidate_batch_id:selection.baseline_batch_id,baseline_version_id:selection.candidate_version_id,baseline_batch_id:selection.candidate_batch_id },true); }
  const query = selection ? new URLSearchParams(Object.entries(selection)).toString() : '';
  return <Context.Provider value={{runs,batches,selection,query,loading,error,select,reload,saveBaseline,swap}}>{children}</Context.Provider>;
}
export function useEvaluationScope() { const value=useContext(Context); if (!value) throw new Error('Evaluation scope provider is missing'); return value; }
export function ScopeSelectors() {
  const { runs,batches,selection,select,loading,error,reload,saveBaseline,swap } = useEvaluationScope(); const { write } = useAccess();
  const unique = (items: ScopeRun[],key:keyof ScopeRun) => items.filter((r,i) => items.findIndex(v => v[key] === r[key]) === i);
  const candidates = runs.filter(r => r.agent_id === selection?.agent_id), sameBenchmark = candidates.filter(r => r.benchmark_version_id === selection?.benchmark_version_id);
  const batchOptions = (version?:string) => batches.filter(b => sameBenchmark.some(r => r.id === b.run_id && r.agent_version_id === version));
  return <section className="panel scope-panel"><ErrorBox message={error}/><div className="scope-selectors">
  <label className="field"><span>Agent 评测身份</span><select aria-label="Agent 评测身份" value={selection?.agent_id || ''} onChange={e => select('agent_id',e.target.value)}><option value="">请选择</option>{unique(runs,'agent_id').map(r => <option key={r.agent_id} value={r.agent_id}>{r.agent_label.split(' · v')[0]}</option>)}</select></label>
  <label className="field"><span>候选版本</span><select aria-label="候选版本" value={selection?.candidate_version_id || ''} disabled={!selection} onChange={e => select('candidate_version_id',e.target.value)}>{unique(candidates,'agent_version_id').map(r => <option key={r.agent_version_id} value={r.agent_version_id}>{r.agent_label}</option>)}</select></label>
  <label className="field"><span>基线版本</span><select aria-label="基线版本" value={selection?.baseline_version_id || ''} disabled={!selection} onChange={e => select('baseline_version_id',e.target.value)}><option value="">未选择基线</option>{unique(sameBenchmark,'agent_version_id').map(r => <option key={r.agent_version_id} value={r.agent_version_id}>{r.agent_label}</option>)}</select></label>
  <label className="field"><span>Benchmark 固定版本</span><select aria-label="Benchmark 固定版本" value={selection?.benchmark_version_id || ''} disabled={!selection} onChange={e => select('benchmark_version_id',e.target.value)}>{unique(candidates.filter(r => r.agent_version_id === selection?.candidate_version_id),'benchmark_version_id').map(r => <option key={r.benchmark_version_id} value={r.benchmark_version_id}>{r.benchmark_label} · {short(r.benchmark_version_id)}</option>)}</select></label><button className="button secondary" disabled={loading} onClick={() => void reload()}>刷新范围</button></div>
  <details><summary>固定评分批次与显式基线</summary><div className="form-grid">{(['candidate','baseline'] as const).map(side => <label className="field" key={side}><span>{side === 'candidate' ? '候选评分批次' : '基线评分批次'}</span><select aria-label={side === 'candidate' ? '候选评分批次' : '基线评分批次'} value={selection?.[`${side}_batch_id`] || ''} onChange={e => select(`${side}_batch_id`,e.target.value)}><option value="">请选择</option>{batchOptions(selection?.[`${side}_version_id`]).map(b => <option key={b.id} value={b.id}>运行 {short(b.run_id)} · 第 {b.revision+1} 批 · {short(b.id)}</option>)}</select></label>)}</div><div className="button-group"><button className="button secondary" disabled={!write || !selection?.baseline_batch_id || loading} onClick={() => void saveBaseline()}>保存为此范围的显式基线</button><button className="button secondary" disabled={!selection?.baseline_version_id} onClick={swap}>交换基线与候选</button></div></details>
  <p className="form-note">{selection?.baseline_version_id ? `比较方向：基线 ${short(selection.baseline_version_id)} → 候选 ${short(selection.candidate_version_id)}。` : '尚未配置或选择基线；不会按运行时间猜测已发布基线。'} 质量、问题及发布记录绑定同一范围，链接包含固定批次。</p></section>;
}
