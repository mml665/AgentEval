import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import type { API } from './api';
import { useAccess } from './Auth';
import { ErrorBox, short } from './ui';

const keys = ['agent_id', 'candidate_version_id', 'baseline_version_id', 'benchmark_version_id',
  'candidate_run_id', 'baseline_run_id', 'candidate_batch_id', 'baseline_batch_id'] as const;
export type Selection = Record<typeof keys[number], string>;
type ScopeRequest = Partial<Selection>;
export interface Option { id: string; label: string }
export interface OptionsPage { items: Option[]; next_offset: number | null }
const readOptions = (api: API, path: string) => api.get<OptionsPage>(path);
type Side = 'candidate' | 'baseline';
interface ScopeResult {
  selection: Selection | null;
  labels?: Partial<Selection>;
  candidate_run_status?: string;
  candidate_batch_status?: string;
  baseline_batch_status?: string;
  baseline_revision?: number;
  options: Partial<Record<keyof Selection, OptionsPage>>;
}

interface ScopeValue {
  result: ScopeResult | null;
  selection: Selection | null;
  query: string;
  loading: boolean;
  error: string;
  select: (field: keyof Selection, value: string) => void;
  reload: () => void;
  saveBaseline: () => Promise<void>;
  swap: () => void;
}

const Context = createContext<ScopeValue | null>(null);
const scoped = (path: string) => /^#\/(overview|issues|releases|regressions)(\/|$)/.test(path) || /^#\/runs\//.test(path);
const search = (value: ScopeRequest) => new URLSearchParams(keys.flatMap(key => value[key] === undefined ? [] : [[key, value[key]!]])).toString();

function fromHash(previous?: Selection | null): ScopeRequest {
  const [path, query = ''] = window.location.hash.split('?');
  const params = new URLSearchParams(query);
  const stored = Object.fromEntries(keys.filter(key => params.has(key)).map(key => [key, params.get(key)!])) as ScopeRequest;
  const run = /^#\/runs\/([^/]+)$/.exec(path)?.[1];
  if (run) {
    const batch = params.get('grading_batch_id');
    if (stored.candidate_run_id !== run) return { candidate_run_id: run, ...(batch ? { candidate_batch_id: batch } : {}) };
    return { ...stored, ...(batch ? { candidate_batch_id: batch } : {}) };
  }
  return Object.keys(stored).length ? stored : previous || {};
}

export function ScopeProvider({ api, projectId, children }: { api: API; projectId: string; children: ReactNode }) {
  const [result, setResult] = useState<ScopeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const current = useRef<{ key: string; value: ScopeResult } | null>(null);
  const pending = useRef<string | null>(null);
  const generation = useRef(0);
  const load = useCallback(async (request: ScopeRequest, force = false, push = false) => {
    const [path, params = ''] = window.location.hash.split('?');
    const requestedProject = new URLSearchParams(params).get('project_id');
    if (!projectId || !scoped(path) || requestedProject && requestedProject !== projectId) return;
    const key = search(request);

    function publish(value: ScopeResult) {
      const [target, query = ''] = window.location.hash.split('?');
      if (!scoped(target)) return;
      const url = new URLSearchParams(query);
      if (url.has('project_id') && url.get('project_id') !== projectId) return;
      current.current = { key: value.selection ? search(value.selection) : key, value };
      setResult(value);
      if (!value.selection) return;
      keys.forEach(name => url.set(name, value.selection![name]));
      url.set('project_id', projectId);
      url.set('scope', '1');
      const next = `${target}?${url}`;
      if (next !== window.location.hash) {
        if (push) window.location.hash = next;
        else history.replaceState(null, '', next);
      }
    }

    if (!force && pending.current === key) return;
    const epoch = ++generation.current;
    pending.current = null;
    setError('');
    if (!force && current.current?.key === key && current.current.value.candidate_batch_status === 'completed') {
      publish(current.current.value);
      setLoading(false);
      return;
    }
    pending.current = key;
    setLoading(true);
    if (current.current?.key !== key) setResult(null);
    try {
      const value = await api.get<ScopeResult>(`quality/scope-selection/${projectId}?${key}`);
      if (epoch === generation.current) publish(value);
    } catch (e) {
      if (epoch === generation.current) {
        setError(`链接中的范围或批次已失效，或暂时无法读取。${(e as Error).message}`);
        if (current.current) setResult({ ...current.current.value, selection: null });
        else {
          try {
            const defaults = await api.get<ScopeResult>(`quality/scope-selection/${projectId}`);
            if (epoch === generation.current) setResult({ ...defaults, selection: null });
          } catch { /* The original scope error remains visible. */ }
        }
      }
    } finally {
      if (epoch === generation.current) { pending.current = null; setLoading(false); }
    }
  }, [api, projectId]);

  useEffect(() => {
    const navigate = () => {
      const path = window.location.hash.split('?')[0];
      if (!scoped(path)) {
        generation.current++;
        pending.current = null;
        setLoading(false);
        return;
      }
      if (/^#\/(releases|regressions)\/[^/]+$/.test(path)) {
        generation.current++;
        pending.current = null;
        setLoading(false);
        return;
      }
      void load(fromHash(current.current?.value.selection));
    };
    const compare = (event: Event) => void load((event as CustomEvent<ScopeRequest>).detail);
    const invalidate = () => {
      generation.current++;
      pending.current = null;
      if (current.current) current.current.key = '';
      setLoading(false);
    };
    window.addEventListener('hashchange', navigate);
    window.addEventListener('agenteval-scope-context', navigate);
    window.addEventListener('agenteval-scope-comparison', compare);
    window.addEventListener('agenteval-scope-invalidated', invalidate);
    navigate();
    return () => {
      generation.current++;
      pending.current = null;
      window.removeEventListener('hashchange', navigate);
      window.removeEventListener('agenteval-scope-context', navigate);
      window.removeEventListener('agenteval-scope-comparison', compare);
      window.removeEventListener('agenteval-scope-invalidated', invalidate);
    };
  }, [load]);

  const selection = result?.selection || null;
  const waiting = result?.candidate_batch_status === 'pending' || result?.candidate_batch_status === 'running'
    || (!result?.candidate_batch_status && ['queued', 'running', 'evaluating', 'cancelling'].includes(result?.candidate_run_status || ''));
  useEffect(() => {
    if (!selection || !waiting) return;
    const timer = setInterval(() => {
      const path = window.location.hash.split('?')[0];
      if (scoped(path) && !/^#\/(releases|regressions)\/[^/]+$/.test(path)) void load(fromHash(selection));
    }, 3500);
    return () => clearInterval(timer);
  }, [selection, waiting, load]);
  function select(field: keyof Selection, value: string) {
    if (field === 'agent_id') {
      void load({ agent_id: value }, true, true);
      return;
    }
    if (!selection) return;
    const { agent_id, candidate_version_id } = selection;
    let next: ScopeRequest = { ...selection, [field]: value };
    if (field === 'candidate_version_id') next = { agent_id, candidate_version_id: value };
    if (field === 'benchmark_version_id') next = { agent_id, candidate_version_id, benchmark_version_id: value };
    if (field === 'baseline_version_id') {
      next.baseline_run_id = '';
      next.baseline_batch_id = '';
    }
    if (field === 'candidate_run_id') next.candidate_batch_id = '';
    if (field === 'baseline_run_id') next.baseline_batch_id = '';
    void load(next, true, true);
  }

  async function saveBaseline() {
    if (!selection?.baseline_batch_id) return;
    setLoading(true);
    try {
      await api.post(`quality/baselines/${projectId}`, {
        agent_id: selection.agent_id, benchmark_version_id: selection.benchmark_version_id,
        grading_batch_id: selection.baseline_batch_id, expected_revision: result?.baseline_revision || 0,
      });
      await load(selection, true);
    } catch (e) { setError((e as Error).message); setLoading(false); }
  }

  function swap() {
    if (!selection?.baseline_run_id) return;
    void load({ ...selection, candidate_version_id: selection.baseline_version_id, candidate_run_id: selection.baseline_run_id,
      candidate_batch_id: selection.baseline_batch_id, baseline_version_id: selection.candidate_version_id,
      baseline_run_id: selection.candidate_run_id, baseline_batch_id: selection.candidate_batch_id }, true, true);
  }

  return <Context.Provider value={{ result, selection, query: selection ? search(selection) : '', loading, error, select,
    reload: () => void load(fromHash(current.current?.value.selection), true), saveBaseline, swap }}>{children}</Context.Provider>;
}

export function useEvaluationScope() {
  const value = useContext(Context);
  if (!value) throw new Error('Evaluation scope provider is missing');
  return value;
}

export function PagedScopeChoice({ api, path, label, value, selectedLabel, changed, disabled, initial, read = readOptions, placeholder = '请选择' }: {
  api: API; path: string; label: string; value: string; selectedLabel?: string;
  changed: (value: string) => void; disabled?: boolean; initial?: OptionsPage; placeholder?: string;
  read?: (api: API, path: string) => Promise<OptionsPage>;
}) {
  const [page, setPage] = useState<OptionsPage | undefined>(initial);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    setPage(initial);
    setError('');
    if (!initial && path) void read(api, path).then(result => { if (alive) setPage(result); }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [api, path, initial, read]);
  async function more() {
    if (page?.next_offset == null) return;
    setBusy(true);
    setError('');
    try {
      const next = await read(api, `${path}&offset=${page.next_offset}`);
      setPage({ ...next, items: [...page.items, ...next.items.filter(item => !page.items.some(old => old.id === item.id))] });
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const items = page?.items || [];
  return <div>
    <label className="field"><span>{label}</span><select aria-label={label} value={value} disabled={disabled || !path}
      onChange={e => changed(e.target.value)}>
      <option value="">{placeholder}</option>
      {value && !items.some(item => item.id === value) && <option value={value}>{selectedLabel || short(value)}</option>}
      {items.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select></label>
    {page?.next_offset != null && <button type="button" className="text-button" disabled={busy || disabled} onClick={() => void more()}>加载更多{label}</button>}
    <ErrorBox message={error}/>
  </div>;
}

export function ScopeSelectors({ api }: { api: API }) {
  const { result, selection, select, loading, error, reload, saveBaseline, swap } = useEvaluationScope();
  const { write, projectId } = useAccess();
  const [advanced, setAdvanced] = useState(false);
  const root = `quality/scope-options/${projectId}?`;
  const agent = selection?.agent_id || '';
  const bv = selection?.benchmark_version_id || '';
  const candidate = selection?.candidate_version_id || '';
  function choice(field: keyof Selection, label: string, path: string, placeholder?: string) {
    return <PagedScopeChoice key={field + path} api={api} path={path} label={label} value={selection?.[field] || ''}
      selectedLabel={result?.labels?.[field]} changed={value => select(field, value)} disabled={loading}
      initial={result?.options[field]} placeholder={placeholder}/>;
  }
  function sideChoices(side: Side) {
    const run = selection?.[`${side}_run_id`];
    const version = selection?.[`${side}_version_id`];
    const label = side === 'candidate' ? '候选' : '基线';
    return <div key={side}>
      {choice(`${side}_run_id`, `${label}运行`, version ? root + `kind=run&agent_version_id=${version}&benchmark_version_id=${bv}` : '')}
      {choice(`${side}_batch_id`, `${label}评分批次`, run ? root + `kind=batch&run_id=${run}` : '')}
    </div>;
  }
  return <section className="panel scope-panel"><ErrorBox message={error}/><div className="scope-selectors">
    {choice('agent_id', 'Agent 评测身份', result ? root + 'kind=agent' : '')}
    {choice('candidate_version_id', '候选版本', agent ? root + `kind=version&agent_id=${agent}` : '')}
    {choice('baseline_version_id', '基线版本', agent ? root + `kind=version&agent_id=${agent}&benchmark_version_id=${bv}` : '', '未选择基线')}
    {choice('benchmark_version_id', 'Benchmark 固定版本', candidate ? root + `kind=benchmark&agent_version_id=${candidate}` : '')}
    <button className="button secondary" disabled={loading} onClick={reload}>刷新范围</button>
  </div>
  <details onToggle={event => setAdvanced(event.currentTarget.open)}><summary>固定评分批次与显式基线</summary>
    {advanced && <div className="form-grid">{(['candidate', 'baseline'] as const).map(sideChoices)}</div>}
    <div className="button-group">
      <button className="button secondary" disabled={!write || result?.baseline_batch_status !== 'completed' || loading} onClick={() => void saveBaseline()}>保存为此范围的显式基线</button>
      <button className="button secondary" disabled={!selection?.baseline_run_id || loading} onClick={swap}>交换基线与候选</button>
    </div>
  </details>
  {selection && result?.candidate_batch_status !== 'completed' && <p role="status">候选运行 {short(selection.candidate_run_id)} 的所选评分证据尚未完成，暂不可比较。</p>}
  <p className="form-note">{selection?.baseline_version_id ? `比较方向：基线 ${short(selection.baseline_version_id)} → 候选 ${short(selection.candidate_version_id)}。` : '尚未配置或选择基线。'} 链接包含固定运行与批次。</p>
  </section>;
}
