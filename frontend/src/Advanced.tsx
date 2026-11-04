import { useEffect, useState } from 'react';
import type { API } from './api';
import type { Workspace } from './workspace';
import { benchmarkName } from './workspace';
import { Badge, Code, Empty, ErrorBox, Heading, Icon, Metric, Modal } from './ui';
import { useAccess } from './Auth';
import { Form } from './Forms';
import type { Benchmark, BenchmarkVersion, Profile, Run } from './types';

interface Component { id: string; name: string; kind: string; capabilities: string[]; input_schema: unknown }
export function ComponentsPage({ api, data, projectId, changed }: { api: API; data: Workspace; projectId: string; changed: () => Promise<void> }) {
  const { write } = useAccess();
  const [components, setComponents] = useState<Component[]>([]), [open, setOpen] = useState(false), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => { void api.get<{ items: Component[] }>('components').then(r => setComponents(r.items)).catch(e => setError(e.message)); }, [api]);
  async function launch(body: unknown) { const run = await api.post<Run>('component-evaluations', body); setOpen(false); await changed(); window.location.hash = `/runs/${run.id}`; }
  async function demo(componentId: string) {
    setBusy(true); setError('');
    try {
      const benchmark = await api.post<Benchmark>('benchmarks', { project_id: projectId, name: '组件检索基准', description: '独立组件示例，不调用业务 Agent。' });
      const version = await api.post<BenchmarkVersion>(`benchmarks/${benchmark.id}/versions`, { version: '1.0', cases: [{
        case_key: 'retrieval-001', input: { query: 'order shipped', k: 1, documents: [
          { id: 'policy-refund', text: 'refund payment' }, { id: 'policy-shipped', text: 'order shipped delivery' }] },
        assertions: { retrieval: { judgment_status: 'complete', relevance: { 'policy-shipped': 2, 'policy-refund': 0 } } }, tags: ['component'] }] });
      const profile = await api.post<Profile>('profile-versions', { project_id: projectId, name: `组件排序评测-${benchmark.id.slice(0, 6)}`, version: '1.0', evaluators: [
        { instance_id: 'recall', id: 'rag', config: { metric: 'recall', k: 1 } }, { instance_id: 'ndcg', id: 'rag', config: { metric: 'ndcg', k: 1 } }] });
      await launch({ project_id: projectId, component_id: componentId, benchmark_version_id: version.id, profile_version_id: profile.id });
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <><Heading eyebrow="COMPONENT EVALUATION" title="独立组件评测" description="直接评测 Retriever / Reranker，复用版本、执行、评分与回归协议。" action={<button className="button primary" disabled={!write} onClick={() => setOpen(true)}><Icon name="plus"/>创建组件评测</button>}/><ErrorBox message={error}/><div className="callout subtle"><Icon name="code"/><span>组件具有独立输入 Schema 和可信部署注册，不依赖业务 Agent。检索标签必须由评测集提供。</span></div><div className="resource-grid">{components.map(c => <section className="resource-card" key={c.id}><div className="card-title"><span className="resource-icon"><Icon name="code"/></span><span className="tag">{c.kind}</span></div><h2>{c.name}</h2><p className="mono">{c.id}</p><details className="component-schema"><summary>输入 Schema</summary><Code value={c.input_schema}/></details><button className="button secondary full" disabled={!write || busy} onClick={() => void demo(c.id)}>{busy ? '处理中…' : '运行组件示例'}<Icon name="run" size={15}/></button></section>)}</div>{open && <Modal title="创建组件评测" onClose={() => setOpen(false)}><Form label="开始组件评测" onCancel={() => setOpen(false)} submit={async form => { const fields = new FormData(form); await launch({ project_id: projectId, component_id: fields.get('component'), benchmark_version_id: fields.get('benchmark'), profile_version_id: fields.get('profile') }); }}><label className="field"><span>组件</span><select name="component" required>{components.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label className="field"><span>数据集版本</span><select name="benchmark" required><option value="">选择符合组件输入格式的版本</option>{data.benchmarkVersions.map(v => <option key={v.id} value={v.id}>{benchmarkName(data, v.id)}</option>)}</select></label><label className="field"><span>评分方案</span><select name="profile" required><option value="">选择评分方案</option>{data.profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.version}</option>)}</select></label><p className="form-note">选择器展示当前资源页的版本。也可以直接运行组件示例，自动创建符合输入 Schema 的测试集。</p></Form></Modal>}</>;
}

interface Health { ready: boolean; mode: string; database: string; broker: string; maintenance: { state: string; alive: boolean; failures: number; error_code: string | null }; services: { instance_id: string; role: string; state: string; last_seen: number; failures: number; error_code: string | null }[]; queue_counts: Record<string, number>; oldest_queue_age_seconds: number }
export function OperationsPage({ api }: { api: API }) {
  const [health, setHealth] = useState<Health | null>(null), [error, setError] = useState('');
  useEffect(() => { let alive = true; async function load() { try { const state = await api.get<Health>('operations/health'); if (alive) { setHealth(state); setError(''); } } catch (e) { if (alive) setError((e as Error).message); } } void load(); const timer = setInterval(() => void load(), 5000); return () => { alive = false; clearInterval(timer); }; }, [api]);
  return <><Heading eyebrow="RUNTIME HEALTH" title="运行健康与恢复" description="区分服务存活与执行就绪，追踪后台循环、队列积压和未知占用。"/><ErrorBox message={error}/>{health ? <><div className="metrics-grid"><Metric label="执行就绪" value={health.ready ? '就绪' : '降级'} hint={`模式：${health.mode}`} icon="activity"/><Metric label="待处理 / 已投递" value={(health.queue_counts.pending || 0) + (health.queue_counts.published || 0)} hint={`最久等待 ${Math.round(health.oldest_queue_age_seconds)} 秒`} icon="clock"/><Metric label="执行中" value={health.queue_counts.running || 0} hint="租约与心跳保护" icon="run"/><Metric label="待确认占用" value={health.queue_counts.blocked || 0} hint="管理员核验后释放，不改变原判定" icon="lock"/></div><section className="panel"><div className="panel-heading"><div><h2>后台服务心跳</h2><p>每 5 秒刷新；超过 15 秒未上报标记 stale</p></div><Badge status={health.ready ? 'passed' : 'inconclusive'}/></div><div className="table-scroll"><table><thead><tr><th>角色 / 实例</th><th>状态</th><th>最后心跳</th><th>连续故障</th><th>错误码</th></tr></thead><tbody>{health.services.map(s => <tr key={s.instance_id}><td>{s.role}<small className="secondary-line mono">{s.instance_id}</small></td><td><Badge status={s.state === 'healthy' ? 'passed' : s.state === 'degraded' ? 'failed' : 'unknown'}/></td><td>{new Date(s.last_seen * 1000).toLocaleTimeString('zh-CN')}</td><td>{s.failures}</td><td className="mono muted">{s.error_code || '—'}</td></tr>)}</tbody></table></div></section><div className="callout"><Icon name="activity"/><span>数据库：{health.database} · Broker：{health.broker} · 维护循环：{health.maintenance.alive ? health.maintenance.state : '已退出'}。临时异常会记录状态并退避重试；未知副作用不会自动重执行。</span></div></> : <Empty title="正在读取运行状态"/>}</>;
}
