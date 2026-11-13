import { useEffect, useState } from 'react';
import type { API } from './api';
import { PagedScopeChoice } from './EvaluationScope';
import { useAccess } from './Auth';
import { Badge, date, Empty, ErrorBox, Heading, Pager, pct } from './ui';

interface Notification {
  id: string;
  created_at: string;
  kind: 'task' | 'issue';
  target_id: string;
  action: string;
  title: string;
  grading_batch_id?: string;
  revision?: number;
  run_status?: string;
}

const actions: Record<string, string> = {
  discovered: '发现待确认异常',
  developer_update: '更新缺陷',
  bulk_ignore: '批量忽略',
  bulk_assign: '批量分配',
  bulk_merge: '合并缺陷',
  scope_changed: '更新适用范围',
  retest_started: '发起复测',
  retest: '记录复测结果',
  promoted_to_regression: '加入回归集',
};
const pageSize = 25;

export function Notifications({ api, projectId }: { api: API; projectId: string }) {
  const { identity } = useAccess();
  const storageKey = `agenteval-notifications:${identity?.id || 'legacy'}:${projectId}`;
  const [seen, setSeen] = useState(() => localStorage.getItem(storageKey) || '');
  const [page, setPage] = useState(0);
  const [result, setResult] = useState<{ items: Notification[]; total: number }>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setResult(undefined);
    async function load() {
      try {
        const value = await api.get<NonNullable<typeof result>>(`quality/notifications/${projectId}?offset=${page * pageSize}&limit=${pageSize}`);
        if (active) { setResult(value); setError(''); }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    }
    void load();
    const timer = page === 0 ? window.setInterval(load, 20000) : undefined;
    return () => { active = false; window.clearInterval(timer); };
  }, [api, projectId, page]);
  const cursor = (item: Notification) => `${item.created_at}:${item.id}`;
  const latest = result?.items[0];
  return <>
    <Heading title="站内通知" description="当前项目的新评分批次和缺陷事件。已读位置保存在当前浏览器。"
      action={<button className="button secondary" disabled={page !== 0 || !latest || cursor(latest) <= seen} onClick={() => {
        if (latest) { setSeen(cursor(latest)); localStorage.setItem(storageKey, cursor(latest)); }
      }}>全部标为已读</button>}/>
    <ErrorBox message={error}/>
    <section className="panel">
      {result?.items.length ? <div className="table-scroll"><table><thead><tr>
        <th>事件</th><th>来源</th><th>时间</th><th>阅读状态</th>
      </tr></thead><tbody>{result.items.map(item => {
        const task = item.kind === 'task';
        const params = new URLSearchParams({ project_id: projectId });
        if (item.grading_batch_id) params.set('grading_batch_id', item.grading_batch_id);
        return <tr key={item.id}>
          <td>{task ? `第 ${(item.revision ?? 0) + 1} 批评分完成` : actions[item.action] || '缺陷事件'}</td>
          <td><a className="table-link" href={`#/${task ? 'runs' : 'issues'}/${item.target_id}?${params}`}>{item.title}</a>
            {item.run_status && <div className="table-secondary"><Badge status={item.run_status}/></div>}</td>
          <td>{date(item.created_at)}</td><td>{cursor(item) > seen ? '未读' : '已读'}</td>
        </tr>;
      })}</tbody></table></div> : result ? <Empty title="暂无通知"/> : !error && <p className="panel-heading" role="status">正在加载通知…</p>}
    </section>
    <Pager page={page} total={result?.total || 0} size={pageSize} loading={!result} onChange={setPage}/>
  </>;
}

interface Trend {
  series: { date: string; planned_trials: number; passed: number; failed: number; success_rate: number | null; decision_coverage: number | null }[];
  tags: { label: string; cases: number; decided_cases: number; coverage: number }[];
  tags_total: number;
}

export function Trends({ api, projectId }: { api: API; projectId: string }) {
  const [agent, setAgent] = useState(''), [version, setVersion] = useState('');
  const [benchmark, setBenchmark] = useState(''), [profile, setProfile] = useState('');
  const [days, setDays] = useState(30), [page, setPage] = useState(0);
  const [result, setResult] = useState<{ query: string; value: Trend }>();
  const [error, setError] = useState('');
  const base = `quality/scope-options/${projectId}?`;
  const path = (kind: string, filters: Record<string, string> = {}) => base + new URLSearchParams({ kind, ...filters });
  const query = version && benchmark && profile ? new URLSearchParams({ agent_version_id: version,
    benchmark_version_id: benchmark, profile_version_id: profile, days: String(days),
    tags_offset: String(page * pageSize), tags_limit: String(pageSize) }).toString() : '';
  const visible = result?.query === query ? result.value : undefined;
  useEffect(() => {
    let active = true;
    setError('');
    if (query) void api.get<Trend>(`quality/trends/${projectId}?${query}`).then(value => {
      if (active) setResult({ query, value });
    }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [api, projectId, query]);
  return <>
    <Heading title="质量趋势" description="固定 Agent 版本、Benchmark 和评分方案后查看按 UTC 日期统计的初始评分结果。"/>
    <div className="panel scope-panel"><div className="scope-selectors">
      <PagedScopeChoice api={api} path={path('agent')} label="Agent" value={agent} changed={id => {
        setAgent(id); setVersion(''); setBenchmark(''); setProfile(''); setPage(0);
      }}/>
      <PagedScopeChoice key={agent} api={api} path={agent ? path('version', { agent_id: agent }) : ''} label="Agent 版本"
        value={version} changed={id => { setVersion(id); setBenchmark(''); setProfile(''); setPage(0); }}/>
      <PagedScopeChoice key={`benchmark:${version}`} api={api} path={version ? path('benchmark', { agent_version_id: version }) : ''}
        label="Benchmark 版本" value={benchmark} changed={id => { setBenchmark(id); setProfile(''); setPage(0); }}/>
      <PagedScopeChoice key={`profile:${version}:${benchmark}`} api={api}
        path={benchmark ? path('profile', { agent_version_id: version, benchmark_version_id: benchmark }) : ''}
        label="评分方案版本" value={profile} changed={id => { setProfile(id); setPage(0); }}/>
      <label className="field"><span>时间范围</span><select value={days} onChange={e => { setDays(Number(e.target.value)); setPage(0); }}>
        {[7, 30, 90, 365].map(n => <option key={n} value={n}>最近 {n} 天</option>)}
      </select></label>
    </div></div>
    <ErrorBox message={error}/>
    {!query ? <div className="panel"><Empty title="请选择评测范围"/></div> : !visible ? !error && <p role="status">正在加载趋势…</p> : <>
      <section className="panel">
        <div className="panel-heading"><div><h2>每日试验结果</h2><p>成功率按已判定试验计算。判定覆盖率按已判定试验占计划试验的比例计算。</p></div></div>
        <div className="table-scroll trend-results"><table><thead><tr><th>日期（UTC）</th><th>计划试验</th><th>通过</th><th>失败</th><th>成功率</th><th>判定覆盖率</th></tr></thead>
          <tbody>{[...visible.series].reverse().map(day => <tr key={day.date}><td>{day.date}</td><td>{day.planned_trials}</td>
            <td>{day.passed}</td><td>{day.failed}</td><td>{pct(day.success_rate)}</td><td>{pct(day.decision_coverage)}</td></tr>)}</tbody>
        </table></div>
      </section>
      <section className="panel">
        <div className="panel-heading"><div><h2>业务标签覆盖</h2><p>每个标签下，至少一次获得通过或失败判定的去重用例，占该标签全部用例的比例。</p></div></div>
        {visible.tags.length ? <div className="table-scroll"><table><thead><tr><th>标签</th><th>已判定用例</th><th>标签用例</th><th>覆盖率</th></tr></thead>
          <tbody>{visible.tags.map(tag => <tr key={tag.label}><td>{tag.label}</td><td>{tag.decided_cases}</td><td>{tag.cases}</td><td>{pct(tag.coverage)}</td></tr>)}</tbody>
        </table></div> : <Empty title="该 Benchmark 版本没有业务标签"/>}
      </section>
      <Pager page={page} total={visible.tags_total} size={pageSize} onChange={setPage}/>
    </>}
  </>;
}
