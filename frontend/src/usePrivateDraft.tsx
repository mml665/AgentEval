import { useEffect, useRef, useState } from 'react';
import type { API } from './api';
import { APIError } from './api';
import { useAccess } from './Auth';
import { ErrorBox } from './ui';

function stable(value: unknown): string {
  const sorted = (v: unknown): unknown => Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).sort(([a],[b]) => a.localeCompare(b)).map(([k,x]) => [k,sorted(x)])) : v;
  return JSON.stringify(sorted(value));
}

export function usePrivateDraft(api: API, projectId: string, payload: object, restore: (value: Record<string,unknown>) => void) {
  const { identity } = useAccess();
  const eligible = identity?.kind === 'user' && Boolean(projectId);
  const [revision,setRevision] = useState(0), [remote,setRemote] = useState<Record<string,unknown> | null>(null), [error,setError] = useState(''), [busy,setBusy] = useState(false), [saved,setSaved] = useState('');
  const [conflict, setConflict] = useState(false), [choose, setChoose] = useState(false);
  const current = stable(payload), initial = useRef(current), savedRef = useRef(current), allowLeave = useRef(false), dirty = current !== savedRef.current;
  useEffect(() => { let live = true; if (eligible) void api.get<{ revision: number; payload: Record<string,unknown> | null }>(`onboarding/drafts/${projectId}`).then(r => { if (live) { setRevision(r.revision); setRemote(r.payload); } }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [api,projectId,eligible]);
  useEffect(() => {
    const origin = window.location.hash;
    const warn = (e: BeforeUnloadEvent) => { if (dirty && !allowLeave.current) { e.preventDefault(); e.returnValue = ''; } };
    const navigate = (e:Event) => { if (window.location.hash.split('?')[0] !== origin.split('?')[0] && dirty && !allowLeave.current) { if (!window.confirm('评测配置有未保存修改，确定离开吗？')) { e.preventDefault(); history.replaceState(null,'',origin); } else allowLeave.current=true; } };
    const logout = (e: Event) => { if (dirty && !window.confirm('评测配置有未保存修改，确定退出吗？')) e.preventDefault(); else allowLeave.current = true; };
    window.addEventListener('beforeunload',warn); window.addEventListener('agenteval-before-navigation',navigate);
    window.addEventListener('agenteval-before-logout',logout); window.addEventListener('agenteval-before-project-change',logout);
    return () => { window.removeEventListener('beforeunload',warn); window.removeEventListener('agenteval-before-navigation',navigate); window.removeEventListener('agenteval-before-logout',logout); window.removeEventListener('agenteval-before-project-change',logout); };
  }, [dirty]);
  async function save(value = payload) {
    if (!eligible) return;
    if (conflict || choose) throw new Error('请先解决个人草稿的修订冲突。');
    setBusy(true); setError('');
    try { const result = await api.post<{ revision: number }>(`onboarding/drafts/${projectId}`, { expected_revision: revision, payload: value }); setRevision(result.revision); savedRef.current = stable(value); setRemote(value as Record<string,unknown>); setSaved('草稿已保存到当前个人与项目。'); }
    catch (e) { if (e instanceof APIError && e.status === 409) setConflict(true); setError((e as Error).message); throw e; } finally { setBusy(false); }
  }
  async function readLatest() {
    setBusy(true); setError(''); setSaved('');
    try {
      const result = await api.get<{ revision: number; payload: Record<string, unknown> | null }>(`onboarding/drafts/${projectId}`);
      savedRef.current = result.payload ? stable(result.payload) : initial.current;
      setRevision(result.revision); setRemote(result.payload); setConflict(false); setChoose(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function restoreRemote() {
    if (!remote) return;
    restore(remote); savedRef.current = stable(remote); setChoose(false); setError('');
    setSaved('已恢复你的服务端草稿。');
  }
  function clean() { allowLeave.current = true; savedRef.current = current; }
  const panel = <div className="callout draft-controls"><div>{eligible ? <>
    <button type="button" className="button secondary" disabled={busy || conflict || choose} onClick={() => void save().catch(() => {})}>保存我的草稿</button>
    <button type="button" className="button secondary" disabled={!remote || busy || conflict} onClick={restoreRemote}>恢复我的草稿</button>
    <span>{dirty ? '有未保存修改' : '配置未修改'} · 修订 {revision}</span>
    {conflict && <button type="button" className="button secondary" disabled={busy} onClick={() => void readLatest()}>读取最新草稿，保留当前输入</button>}
    {choose && <div><p>已读取修订 {revision}，编辑区仍是当前输入。请选择恢复服务端草稿，或继续保留当前配置。</p>
      <button type="button" className="button secondary" onClick={() => { setChoose(false); setSaved('已保留当前输入，下次保存会更新刚读取的草稿修订。'); }}>保留当前配置</button></div>}
  </> : <span>草稿需要个人账户和已选项目；当前配置只保留在页面内存。</span>}{saved && <p role="status">{saved}</p>}<ErrorBox message={error}/></div></div>;
  return { save, clean, panel, eligible, dirty, initial: initial.current };
}
