import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { createAPI } from './api';
import type { Agent, Benchmark, Project, Run } from './types';
import { useWorkspace } from './workspace';
import { seedExample } from './sample';
import { AgentForm, AgentVersionForm, BenchmarkForm, CompareForm, ImportForm, ProfileForm, ProjectForm, RunForm } from './Forms';
import { AgentsPage, BenchmarksPage, Overview, ProfilesPage, RegressionsPage, RunsPage, type Action } from './Pages';
import { ComparisonDetail, RunDetail } from './Details';
import { ComponentsPage, OperationsPage } from './Advanced';
import { Pager } from './ui';
import { Empty, ErrorBox, Icon, Logo, Modal } from './ui';

const navigation = [
  { path: '/overview', label: '评测概览', icon: 'grid' },
  { path: '/runs', label: '评测任务', icon: 'run' },
  { path: '/agents', label: 'Agent 管理', icon: 'agent' },
  { path: '/benchmarks', label: 'Benchmark', icon: 'data' },
  { path: '/profiles', label: '评分方案', icon: 'sliders' },
  { path: '/regressions', label: '版本回归', icon: 'compare' },
  { path: '/components', label: '组件评测', icon: 'code' },
  { path: '/operations', label: '运行健康', icon: 'activity' },
];

function Login({ connected }: { connected: (token: string) => void }) {
  const [token, setToken] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function connect(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await createAPI(token, () => {}).get('capabilities'); connected(token); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="login-page"><div className="login-brand"><Logo/><strong>AgentEval<span> / CONSOLE</span></strong></div><main className="login-content"><section className="login-story"><span className="eyebrow">BUILD WITH CONFIDENCE</span><h1>让 Agent 的每一次进化，<br/><span>都有证据可循。</span></h1><p>统一评测不同实现的 Agent。追踪执行过程，解释失败原因，在版本迭代中守住质量基线。</p><div className="login-flow">{[['agent', 'Agent', '异构接入'], ['run', 'Evaluate', '统一执行'], ['activity', 'Evidence', '评分与回归']].map(([icon, label, hint], i) => <div key={label} className="login-flow-item"><span><Icon name={icon} size={24}/></span><strong>{label}</strong><small>{hint}</small>{i < 2 && <i className="flow-line"/>}</div>)}</div><div className="login-note"><span className="live-dot"/>规则优先 · 过程可观测 · 版本可追溯</div></section><section className="login-card"><span className="resource-icon"><Icon name="lock" size={22}/></span><h2>连接评测工作台</h2><p>使用当前部署的团队访问令牌登录。</p><form onSubmit={connect}><label className="field"><span>访问令牌</span><input type="password" autoComplete="off" placeholder="输入 AGENTEVAL_API_TOKEN" value={token} onChange={e => setToken(e.target.value)} minLength={16} required autoFocus/></label><ErrorBox message={error}/><button className="button primary full" disabled={busy}>{busy ? '正在连接…' : '进入工作台'}<Icon name="arrow"/></button></form><div className="login-security"><Icon name="lock" size={14}/><span>令牌仅保留在页面内存中，不写入浏览器存储。</span></div><span className="login-version">LOCAL WORKSPACE · HTTP / PYTHON</span></section></main><footer className="login-footer"><span>AgentEval / Agent Evaluation Infrastructure</span><span>评测，是迭代的起点。</span></footer></div>;
}

type Dialog = { kind: Action | 'agent-version' | 'import'; agent?: Agent; benchmark?: Benchmark };

export function App() {
  const [token, setToken] = useState('');
  const [runtime, setRuntime] = useState({ execution_mode: 'local', judge_configured: false });
  const disconnect = useCallback(() => setToken(''), []);
  const api = useMemo(() => createAPI(token, disconnect), [token, disconnect]);
  const [projects, setProjects] = useState<Project[]>([]), [projectId, setProjectId] = useState('');
  const [route, setRoute] = useState(() => window.location.hash.slice(1) || '/overview');
  const [dialog, setDialog] = useState<Dialog | null>(null), [error, setError] = useState('');
  const [seeding, setSeeding] = useState(false), [mobileNav, setMobileNav] = useState(false);
  const { data, loading, error: dataError, reload, pages, setPage } = useWorkspace(api, token ? projectId : '');
  const reloadProjects = useCallback(async () => { const list = await api.all<Project>('projects'); setProjects(list); return list; }, [api]);
  useEffect(() => { const listener = () => { setRoute(window.location.hash.slice(1) || '/overview'); setMobileNav(false); }; window.addEventListener('hashchange', listener); return () => window.removeEventListener('hashchange', listener); }, []);
  useEffect(() => { if (!token) { setProjectId(''); setProjects([]); setDialog(null); return; } let alive = true; void api.all<Project>('projects').then(list => { if (!alive) return; setProjects(list); setProjectId(list[0]?.id || ''); }).catch(e => { if (alive) setError((e as Error).message); }); return () => { alive = false; }; }, [api, token]);
  useEffect(() => { if (token) void api.get<typeof runtime>('capabilities').then(setRuntime).catch(() => {}); }, [api, token]);
  const project = projects.find(p => p.id === projectId);
  function action(kind: Action) { if (!projectId && kind !== 'project') { setDialog({ kind: 'project' }); return; } setDialog({ kind }); }
  async function saved() { setDialog(null); await reload(); }
  async function seed() {
    setSeeding(true); setError('');
    try { const created = await seedExample(api); await reloadProjects(); setProjectId(created.id); window.location.hash = '/overview'; }
    catch (e) { setError((e as Error).message); await reloadProjects(); }
    finally { setSeeding(false); }
  }
  if (!token) return <Login connected={value => { setError(''); setToken(value); }}/>;
  const activeNav = navigation.find(n => route === n.path || route.startsWith(n.path + '/')) || navigation[0];
  const close = () => setDialog(null);
  const common = { data, api, action };
  const modalTitles: Record<Dialog['kind'], string> = { project: '新建项目', agent: '注册 Agent', 'agent-version': '发布 Agent 版本', benchmark: '创建 Benchmark', import: '导入用例版本', profile: '创建评分方案', run: '创建评测任务', compare: '创建版本比较' };
  return <div className="app-shell"><aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}><a href="#/overview" className="brand"><Logo/><strong>AgentEval<span>评测与回归分析平台</span></strong></a><div className="sidebar-caption">WORKSPACE</div><nav aria-label="主导航">{navigation.map(n => <a key={n.path} href={`#${n.path}`} className={activeNav.path === n.path ? 'nav-item active' : 'nav-item'} aria-current={activeNav.path === n.path ? 'page' : undefined}><Icon name={n.icon}/>{n.label}{n.path === '/runs' && data.runs.length > 0 && <span className="nav-count">{data.runs.length}</span>}</a>)}</nav><div className="sidebar-bottom"><div className="engine-card"><span className="live-dot"/><div><strong>{runtime.execution_mode === 'celery' ? '独立 Worker 队列' : '本地持久化执行'}</strong><small>HTTP / Python / LangGraph</small></div><span className="tag">M2</span></div><button className="sidebar-signout" onClick={disconnect}><span className="avatar">AE</span><span>团队工作台<small>已连接当前部署</small></span><Icon name="exit"/></button></div></aside>{mobileNav && <button className="nav-backdrop" aria-label="收起导航" onClick={() => setMobileNav(false)}/>}<div className="main-shell"><header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobileNav(true)}><Icon name="grid"/></button><Icon name="folder" size={16}/><span>工作台</span><span className="slash">/</span><strong>{activeNav.label}</strong></div><div className="topbar-actions"><label className="project-picker"><span className="sr-only">当前项目</span><select aria-label="当前项目" value={projectId} onChange={e => { setProjectId(e.target.value); window.location.hash = '/overview'; }}>{!projects.length && <option value="">尚无项目</option>}{projects.map(p => <option value={p.id} key={p.id}>{p.name}</option>)}</select></label><button className="icon-button" aria-label="新建项目" title="新建项目" onClick={() => action('project')}><Icon name="plus"/></button><span className="topbar-divider"/><button className={`icon-button ${loading ? 'spinning' : ''}`} aria-label="刷新数据" onClick={() => void reload()} disabled={loading}><Icon name="refresh"/></button></div></header><main className="content"><ErrorBox message={error || dataError}/>{data.versions_truncated && <div className="callout">版本列表已达到单次加载上限（500），请使用版本分页接口查看更早版本。</div>}{seeding && <div className="progress-banner" role="status"><span className="loading-dot"/>正在创建示例资源并执行真实评测，请稍候…</div>}{!projectId ? <div className="welcome"><span className="eyebrow">YOUR FIRST EVALUATION</span><h1>从一个项目开始，<br/>建立 Agent 的质量基线。</h1><p>连接 Agent、准备测试集、运行评测。你也可以先使用订单示例，体验完整的执行与回归流程。</p><div className="button-group"><button className="button primary" onClick={() => action('project')}><Icon name="plus"/>新建项目</button><button className="button secondary" disabled={seeding} onClick={() => void seed()}><Icon name="run"/>创建演示项目</button></div><div className="welcome-steps">{[['01', '接入 Agent', 'HTTP API 或可信 Python 插件'], ['02', '定义评测', '固定 Benchmark 与评分方案'], ['03', '追溯与比较', '查看执行证据，识别版本退化']].map(([n, title, desc]) => <div key={n}><span>{n}</span><h3>{title}</h3><p>{desc}</p></div>)}</div></div> : <><div className="project-context"><span className="context-dot"/>{project?.name || '项目'}{project?.description && <span className="project-description">{project.description}</span>}{!data.runs.length && !loading && <button className="text-button" disabled={seeding} onClick={() => void seed()}>体验演示项目<Icon name="arrow" size={14}/></button>}</div>{route.startsWith('/runs/') ? <RunDetail key={route} id={route.split('/')[2]} api={api} data={data} changed={reload}/> : route.startsWith('/regressions/') ? <ComparisonDetail key={route} id={route.split('/')[2]} api={api}/> : route === '/operations' ? <OperationsPage api={api}/> : route === '/components' ? <ComponentsPage api={api} data={data} projectId={projectId} changed={reload}/> : route === '/agents' ? <AgentsPage {...common} addVersion={agent => setDialog({ kind: 'agent-version', agent })}/> : route === '/benchmarks' ? <BenchmarksPage {...common} importVersion={benchmark => setDialog({ kind: 'import', benchmark })}/> : route === '/profiles' ? <ProfilesPage {...common}/> : route === '/runs' ? <RunsPage {...common}/> : route === '/regressions' ? <RegressionsPage {...common}/> : route === '/overview' ? <Overview {...common}/> : <Empty title="页面不存在" description="请使用左侧导航返回评测工作台。"/>}</>}{projectId && ['agents', 'benchmarks', 'profiles', 'runs', 'regressions'].includes(route.slice(1)) && <Pager page={pages[route === '/regressions' ? 'comparisons' : route.slice(1)] || 0} total={data.totals?.[route === '/regressions' ? 'comparisons' : route.slice(1)] || 0} size={10} onChange={page => setPage(route === '/regressions' ? 'comparisons' : route.slice(1), page)}/>}<footer className="workspace-footer"><span><span className="live-dot"/>连接到当前部署</span><span>证据驱动的 Agent 质量评测 <span className="mono">/ 0.5.1</span></span></footer></main></div>{dialog && <Modal title={modalTitles[dialog.kind]} onClose={close} wide={['import', 'profile', 'compare'].includes(dialog.kind)}>{dialog.kind === 'project' ? <ProjectForm api={api} close={close} saved={async id => { await reloadProjects(); setProjectId(id); close(); window.location.hash = '/overview'; }}/> : dialog.kind === 'agent' ? <AgentForm api={api} projectId={projectId} saved={saved} close={close}/> : dialog.kind === 'agent-version' && dialog.agent ? <AgentVersionForm api={api} agent={dialog.agent} saved={saved} close={close}/> : dialog.kind === 'benchmark' ? <BenchmarkForm api={api} projectId={projectId} saved={saved} close={close}/> : dialog.kind === 'import' && dialog.benchmark ? <ImportForm api={api} benchmark={dialog.benchmark} saved={saved} close={close}/> : dialog.kind === 'profile' ? <ProfileForm api={api} projectId={projectId} saved={saved} close={close}/> : dialog.kind === 'run' ? <RunForm api={api} data={data} close={close} saved={async (run: Run) => { close(); await reload(); window.location.hash = `/runs/${run.id}`; }}/> : dialog.kind === 'compare' ? <CompareForm api={api} data={data} close={close} saved={async id => { close(); await reload(); window.location.hash = `/regressions/${id}`; }}/> : null}</Modal>}</div>;
}
