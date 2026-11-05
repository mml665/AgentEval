import { createContext, useContext, useEffect, useState, type ReactNode, type FormEvent } from 'react';
import { createAPI, type API } from './api';
import { ErrorBox, Logo } from './ui';

export interface Identity { kind: string; id: string | null; display_name: string; is_admin: boolean; project_roles: Record<string, string>; csrf_token: string; session_id: string | null; legacy: boolean }
export interface AuthOptions { mode: string; legacy_enabled: boolean; initialized: boolean }
export const AuthContext = createContext<{ identity: Identity; projectId: string } | null>(null);
export function useAccess() {
  const value = useContext(AuthContext);
  return { identity: value?.identity, admin: Boolean(value?.identity.is_admin),
    projectId: value?.projectId || '',
    write: Boolean(value?.identity.is_admin || value?.identity.project_roles[value?.projectId || ''] === 'evaluator') };
}

export function Authentication({ options, legacy, connected }: { options: AuthOptions; legacy: ReactNode; connected: (identity: Identity) => void }) {
  const [personal, setPersonal] = useState(options.mode === 'team'), [username, setUsername] = useState(''), [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { const api = createAPI('', () => {}); await api.post('auth/login', { username, password }); setPassword(''); connected(await api.get<Identity>('auth/session')); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  if (!personal && options.legacy_enabled) return <>{legacy}<button className="button auth-switch" onClick={() => setPersonal(true)}>使用个人账号登录</button></>;
  return <div className="login-page"><div className="login-brand"><Logo/><strong>AgentEval</strong></div><main className="personal-login"><section className="login-card"><h1>团队账号登录</h1><p>会话由服务端管理，可过期、撤销和安全退出。</p><form onSubmit={submit}><label className="field"><span>用户名</span><input autoComplete="username" required maxLength={80} value={username} onChange={e => setUsername(e.target.value)}/></label><label className="field"><span>密码</span><input type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={e => setPassword(e.target.value)}/></label><ErrorBox message={error}/><button className="button primary" disabled={busy}>{busy ? '正在验证…' : '登录团队工作台'}</button></form>{!options.initialized && <p>首位管理员需在部署端执行 init-admin 初始化；此处不提供公开注册。</p>}{options.legacy_enabled && <button className="button secondary" onClick={() => setPersonal(false)}>使用兼容部署 Token</button>}</section></main></div>;
}

export function Members({ api, projectId, value, changed, disabled = false, label = '负责人' }: { api: API; projectId: string; value: string; changed: (id: string) => void; disabled?: boolean; label?: string }) {
  // The component is loaded per project and never persists a member list in browser storage.
  return <MemberOptions key={projectId} {...{ api, projectId, value, changed, disabled, label }}/>;
}

function MemberOptions({ api, projectId, value, changed, disabled, label }: { api: API; projectId: string; value: string; changed: (id: string) => void; disabled: boolean; label: string }) {
  const [members, setMembers] = useState<{ id: string; display_name: string; active: boolean }[]>([]), [error, setError] = useState('');
  useEffect(() => { let alive = true; void api.get<{ items: typeof members }>(`team/members?project_id=${projectId}`).then(r => { if (alive) setMembers(r.items); }).catch(e => { if (alive) setError(e.message); }); return () => { alive = false; }; }, [api, projectId]);
  return <label className="field"><span>{label}</span><select aria-label={label} disabled={disabled} value={value} onChange={e => changed(e.target.value)}><option value="">未分配</option>{value && !members.some(m => m.id === value) && <option value={value}>历史成员（请重新分配）</option>}{members.map(m => <option key={m.id} value={m.id} disabled={!m.active}>{m.display_name}{!m.active ? '（已停用）' : ''}</option>)}</select><ErrorBox message={error}/></label>;
}
