import { useEffect, useState } from 'react';
import type { API } from './api';
import { useAccess } from './Auth';
import { Badge, Code, date, ErrorBox, Pager, short } from './ui';

interface Policy {
  revision: number;
  definition: {
    paths: string[];
    secret_env_names: string[];
    redact_emails: boolean;
    retention_days: number | null;
    evidence_warning_bytes?: number | null;
  };
}

interface Grant { user_id: string; raw_read: boolean }

interface Inventory {
  runs: number;
  release_referenced_runs: number;
  retention_days: number | null;
  past_retention_runs: number;
  past_retention_release_holds: number;
  evidence_json_bytes: number;
  evidence_json_bytes_by_kind: Record<'responses' | 'trace' | 'state' | 'scores', number>;
  evidence_warning_bytes: number | null;
  warning_reached: boolean;
}

interface InventoryRun {
  id: string;
  created_at: string;
  status: string;
  past_retention: boolean;
  release_referenced: boolean;
}

const integer = new Intl.NumberFormat('zh-CN');
const groups = [
  ['responses', '执行响应'], ['trace', 'Trace'], ['state', '状态快照'], ['scores', '评分结果'],
] as const;

function EvidenceInventory({ api, projectId, revision }: { api: API; projectId: string; revision: number }) {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [runs, setRuns] = useState<{ items: InventoryRun[]; total: number } | null>(null);
  const [review, setReview] = useState<'overdue' | 'referenced'>('overdue');
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [summaryError, setSummaryError] = useState('');
  const [rowsError, setRowsError] = useState('');
  const size = 25;
  const root = `protection/storage/${projectId}`;

  useEffect(() => {
    let live = true;
    setInventory(null);
    setSummaryError('');
    void api.get<Inventory>(root).then(value => {
      if (live) setInventory(value);
    }).catch(error => { if (live) setSummaryError(error.message); });
    return () => { live = false; };
  }, [api, root, revision, refresh]);

  useEffect(() => {
    let live = true;
    setRuns(null);
    setRowsError('');
    void api.get<{ items: InventoryRun[]; total: number }>(`${root}/runs?review=${review}&offset=${page * size}&limit=${size}`).then(value => {
      if (!live) return;
      if (page && page * size >= value.total) setPage(Math.max(0, Math.ceil(value.total / size) - 1));
      else setRuns(value);
    }).catch(error => { if (live) setRowsError(error.message); });
    return () => { live = false; };
  }, [api, root, review, page, revision, refresh]);

  return <div className="form-body">
    <div className="button-group"><h3>项目证据容量</h3>
      <button className="button secondary" type="button" onClick={() => setRefresh(value => value + 1)}>刷新容量与留存</button>
    </div>
    <ErrorBox message={summaryError}/>
    {!inventory && !summaryError && <p role="status">正在统计证据容量…</p>}
    {inventory && <>
      <p>证据 JSON 合计 <strong>{integer.format(inventory.evidence_json_bytes)} 字节</strong>，按数据库序列化文本统计。</p>
      <p>{groups.map(([key, label]) => `${label} ${integer.format(inventory.evidence_json_bytes_by_kind[key])} 字节`).join(' · ')}</p>
      <p className={inventory.warning_reached ? 'callout' : 'muted'} role={inventory.warning_reached ? 'status' : undefined}>
        {inventory.evidence_warning_bytes == null ? '容量提醒阈值待设置。' :
          `${inventory.warning_reached ? '已达到容量提醒阈值' : '当前低于容量提醒阈值'}（${integer.format(inventory.evidence_warning_bytes)} 字节）。`}
      </p>
      <p>运行 {inventory.runs} 条 · 发布引用 {inventory.release_referenced_runs} 条 · 超期 {inventory.past_retention_runs} 条，其中发布引用 {inventory.past_retention_release_holds} 条。</p>
      <p>{inventory.retention_days == null ? '当前留存期限待设置。' : `留存提醒期限 ${inventory.retention_days} 天。`} 发布快照依赖的证据继续保留。归档由部署侧 CLI 执行。</p>
    </>}
    <label className="field"><span>留存核查范围</span><select value={review} onChange={event => {
      setReview(event.target.value as typeof review); setPage(0);
    }}><option value="overdue">超过留存期限</option><option value="referenced">发布快照引用</option></select></label>
    <ErrorBox message={rowsError}/>
    {!runs && !rowsError && <p role="status">正在读取留存核查记录…</p>}
    {runs && (runs.items.length ? <div className="table-scroll"><table>
      <thead><tr><th>评测运行</th><th>创建时间</th><th>执行状态</th><th>留存状态</th></tr></thead>
      <tbody>{runs.items.map(run => <tr key={run.id}>
        <td><a href={`#/runs/${run.id}?project_id=${projectId}`}>运行 {short(run.id)}</a></td>
        <td>{date(run.created_at)}</td><td><Badge status={run.status}/></td>
        <td>{run.past_retention ? '已超留存期限' : '发布引用'}{run.past_retention && run.release_referenced && ' · 发布引用'}</td>
      </tr>)}</tbody>
    </table></div> : <p>当前核查范围暂无运行。</p>)}
    {runs && runs.total > size && <Pager page={page} total={runs.total} size={size} onChange={setPage}/>}
  </div>;
}

export function ProtectionPanel({ api, projectId }: { api: API; projectId: string }) {
  const { admin, identity } = useAccess();
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [paths, setPaths] = useState('');
  const [names, setNames] = useState('');
  const [days, setDays] = useState('');
  const [warning, setWarning] = useState('');
  const [emails, setEmails] = useState(true);
  const [members, setMembers] = useState<{ id: string; display_name: string; active: boolean }[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [inventoryOpen, setInventoryOpen] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let live = true;
    void Promise.all([
      api.get<Policy>(`protection/policy/${projectId}`),
      api.get<{ items: Grant[] }>(`protection/grants/${projectId}`),
      api.get<{ items: typeof members }>(`team/members?project_id=${projectId}`),
    ]).then(([value, grantList, memberList]) => {
      if (!live) return;
      setPolicy(value);
      setPaths(value.definition.paths.join('\n'));
      setNames(value.definition.secret_env_names.join('\n'));
      setDays(value.definition.retention_days?.toString() || '');
      setWarning(value.definition.evidence_warning_bytes?.toString() || '');
      setEmails(value.definition.redact_emails);
      setGrants(grantList.items);
      setMembers(memberList.items);
    }).catch(error => { if (live) setError(error.message); });
    return () => { live = false; };
  }, [api, projectId]);

  async function save() {
    if (!policy) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await api.post<{ revision: number }>(`protection/policy/${projectId}`, {
        expected_revision: policy.revision, paths: paths.split('\n').map(value => value.trim()).filter(Boolean),
        secret_env_names: names.split('\n').map(value => value.trim()).filter(Boolean),
        redact_emails: emails, retention_days: days ? Number(days) : null, evidence_warning_bytes: warning ? Number(warning) : null,
      });
      setPolicy({ ...policy, revision: result.revision });
      setMessage(`已发布策略修订 ${result.revision}，重新读取业务页面后应用。`);
    } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }

  async function assign(userId: string, rawRead: boolean) {
    setBusy(true); setError('');
    try {
      await api.post(`protection/grants/${projectId}`, { user_id: userId, raw_read: rawRead });
      setGrants((await api.get<{ items: Grant[] }>(`protection/grants/${projectId}`)).items);
    } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }

  return <section className="panel">
    <div className="panel-heading"><h2>证据保护与留存</h2></div>
    <div className="form-body"><ErrorBox message={error}/>
      <p>业务展示和报告使用脱敏派生视图，原始证据及哈希保留。原文只通过单独授权、填写理由的审计入口读取。</p>
      {policy && <p>当前策略修订：{policy.revision}。{identity?.kind === 'legacy_team' && '共享 Token 不能读取原文，请使用个人账户。'}</p>}
      {admin && policy && <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <label className="field"><span>字段路径（每行一个；支持 * 单层、** 多层）</span><textarea value={paths} onChange={event => setPaths(event.target.value)} placeholder="/**/customer/phone" rows={3}/></label>
        <label className="field"><span>内容秘密的服务端变量名（每行一个，不填秘密值）</span><textarea value={names} onChange={event => setNames(event.target.value)} placeholder="AGENTEVAL_REDACTION_SECRET_CUSTOMER" rows={2}/></label>
        <label className="field"><span>留存天数（留空不设期限；仅提醒，不自动删除）</span><input type="number" min={1} max={36500} value={days} onChange={event => setDays(event.target.value)}/></label>
        <label className="field"><span>证据容量提醒阈值（字节）</span><input type="number" min={1} max={2 ** 50} value={warning} onChange={event => setWarning(event.target.value)} placeholder="留空关闭提醒"/></label>
        <label><input type="checkbox" checked={emails} onChange={event => setEmails(event.target.checked)}/> 遮蔽文本中的电子邮箱</label>
        <p>内容变量须由部署方配置，使用 AGENTEVAL_REDACTION_SECRET_ 前缀及凭据指纹密钥。变量缺失或原值改变时，业务视图会拒绝返回。</p>
        <button className="button primary" disabled={busy}>发布新的保护策略</button>
      </form>}
      {message && <p role="status">{message}</p>}
      {admin && <details><summary>原始证据专项权限</summary>
        <p>只对有效个人项目成员授权。</p>
        {members.filter(member => member.active).map(member => {
          const granted = grants.some(grant => grant.user_id === member.id && grant.raw_read);
          return <div className="session-row" key={member.id}><span>{member.display_name}</span>
            <button className="button secondary" disabled={busy} onClick={() => void assign(member.id, !granted)}>{granted ? '撤销原文读取' : '授权原文读取'}</button></div>;
        })}
      </details>}
      <details onToggle={event => setInventoryOpen(event.currentTarget.open)}>
        <summary>容量、期限与报告引用</summary>
        {inventoryOpen && <EvidenceInventory api={api} projectId={projectId} revision={policy?.revision || 0}/>}
      </details>
    </div>
  </section>;
}

export function RawEvidence({ api, runId }: { api: API; runId: string }) {
  const { identity, projectId } = useAccess();
  const [allowed,setAllowed] = useState(false), [reason,setReason] = useState(''), [value,setValue] = useState<{ view: { audit_id: string }; evidence: unknown } | null>(null), [error,setError] = useState(''), [busy,setBusy] = useState(false);
  useEffect(() => { let live = true; if (identity?.kind !== 'user') return; void api.get<{ items: Grant[]; personal_admin_raw_read: boolean }>(`protection/grants/${projectId}`).then(g => { if (live) setAllowed(g.personal_admin_raw_read || g.items.some(v => v.user_id === identity.id && v.raw_read)); }).catch(() => {}); return () => { live = false; setValue(null); }; }, [api,projectId,identity?.id,identity?.kind]);
  if (!allowed) return null;
  return <section className="panel"><div className="panel-heading"><h2>受审计的原始证据</h2></div><details className="form-body"><summary>按授权读取原文（需理由）</summary><p>此入口会显示未脱敏数据，每次读取记录个人身份、运行和理由。请在受控环境中查看。</p><ErrorBox message={error}/>{value ? <><p>原文读取审计：{short(value.view.audit_id)}</p><button className="button secondary" onClick={() => { setValue(null); setReason(''); }}>关闭并清除原文</button><Code value={value}/></> : <form onSubmit={e => { e.preventDefault(); setBusy(true); setError(''); void api.post<typeof value>(`protection/evaluations/${runId}/raw`, { reason }).then(setValue).catch(e => setError(e.message)).finally(() => setBusy(false)); }}><label className="field"><span>原文读取理由（至少 8 个字符）</span><input required minLength={8} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} autoComplete="off"/></label><button className="button secondary" disabled={busy}>读取原始证据并记录审计</button></form>}</details></section>;
}
