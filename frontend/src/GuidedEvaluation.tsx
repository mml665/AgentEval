import { useEffect, useRef, useState } from 'react';
import { APIError, type API } from './api';
import type { AgentVersion, BenchmarkVersion, Page, Profile, Run } from './types';
import { CredentialSelector } from './HttpAccess';
import { sampleCases } from './sample';
import { CaseEditor, parseCases } from './CaseEditor';
import { usePrivateDraft } from './usePrivateDraft';
import { PagedScopeChoice } from './EvaluationScope';
import { ErrorBox, Heading, Icon, Code } from './ui';

interface Options { python: string[]; langgraph: string[]; environments: string[] }
interface Preview { agent_label: string; benchmark_label: string; profile_label: string; case_count: number; planned_trials: number; execution: { repetitions: number; timeout_seconds: number; max_concurrency: number }; judge: { initial_calls: number; possible_calls_with_retry: number; budget: number } }
const goalsList = [['answer','回答正确性',''],['tools','工具调用','tool_calls'],['trajectory','执行轨迹','workflow_steps'],['retrieval','RAG 检索','retrieval_results'],['state','独立状态验证','']];

function resourceOptions<T extends { id: string }>(label: (row: T) => string) {
  return async (api: API, path: string) => {
    const limit = 50;
    const offset = Number(new URLSearchParams(path.split('?')[1]).get('offset') || 0);
    const page = await api.get<Page<T>>(`${path}&limit=${limit}`);
    const end = offset + page.items.length;
    return { items: page.items.map(row => ({ id: row.id, label: label(row) })),
      next_offset: (page.total !== undefined ? end < page.total : page.items.length === limit) ? end : null };
  };
}
const readAgents = resourceOptions<AgentVersion & { agent_name: string }>(row => `${row.agent_name} · v${row.version}`);
const readBenchmarks = resourceOptions<BenchmarkVersion>(row => `${row.benchmark_name || 'Benchmark'} · v${row.version} · ${row.case_count} 条 · ${row.id.slice(0, 8)}`);
const readProfiles = resourceOptions<Profile>(row => `${row.name} · v${row.version}`);

export function GuidedEvaluation({ api, projectId, source, startMode, finished }: { api: API; projectId: string; source: string | null; startMode: string | null; finished: (run: Pick<Run, 'id' | 'project_id'>) => Promise<void> }) {
  const [mode,setMode] = useState(source || startMode === 'reuse' && projectId ? 'reuse' : 'new'), [step,setStep] = useState(0), [options,setOptions] = useState<Options>({ python:[],langgraph:[],environments:[] });
  const [credential,setCredential] = useState(''), [adapter,setAdapter] = useState('python'), [plugin,setPlugin] = useState(''), [endpoint,setEndpoint] = useState('');
  const [name,setName] = useState('我的 Agent'), [version,setVersion] = useState('1.0'), [projectName,setProjectName] = useState('我的质量项目');
  const [capabilities,setCapabilities] = useState<string[]>(['tool_calls','workflow_steps','retrieval_results']), [goals,setGoals] = useState<string[]>(['answer']), [environment,setEnvironment] = useState('');
  const [cases,setCases] = useState(''), [execution,setExecution] = useState({ repetitions:1,timeout_seconds:30,max_concurrency:4,max_retries:0,tool_faults:[] });
  const [av,setAv] = useState(''), [bv,setBv] = useState(''), [profile,setProfile] = useState('');
  const [requestKey,setRequestKey] = useState<string>(() => crypto.randomUUID()), [preview,setPreview] = useState<Preview | null>(null), [unknown,setUnknown] = useState(false), [queried,setQueried] = useState(false);
  const [error,setError] = useState(''), [busy,setBusy] = useState(false), [connection,setConnection] = useState('');
  const sourceEdited = useRef(false);
  function change() { sourceEdited.current = true; setRequestKey(crypto.randomUUID()); setPreview(null); setError(''); setQueried(false); }
  const payload = { mode,step,request_key:requestKey,adapter,plugin,endpoint,credential_version_id:credential || null,name,version,capabilities,goals,environment,cases,agent_version_id:av || null,benchmark_version_id:bv || null,profile_version_id:profile || null,execution,pending_submission:unknown };
  const draft = usePrivateDraft(api,projectId,payload,v => { sourceEdited.current = true; setMode(String(v.mode)); setStep(Number(v.step)); setRequestKey(String(v.request_key)); setAdapter(String(v.adapter)); setPlugin(String(v.plugin)); setEndpoint(String(v.endpoint)); setCredential(String(v.credential_version_id || '')); setName(String(v.name)); setVersion(String(v.version)); setCapabilities(v.capabilities as string[]); setGoals(v.goals as string[]); setEnvironment(String(v.environment)); setCases(String(v.cases)); setAv(String(v.agent_version_id || '')); setBv(String(v.benchmark_version_id || '')); setProfile(String(v.profile_version_id || '')); if (v.execution) setExecution(v.execution as typeof execution); setUnknown(Boolean(v.pending_submission)); setQueried(false); setPreview(null); });
  useEffect(() => { let live = true; void api.get<Options>('onboarding/options').then(v => { if (live) setOptions(v); }).catch(e => { if (live) setError(e.message); });
    if (source) void api.get<{ project_id:string; agent_version_id:string; benchmark_version_id:string; profile_version_id:string }>(`evaluations/${source}/reuse-config`).then(v => { if (!live || sourceEdited.current) return; if (v.project_id !== projectId) { setError('复制来源与当前项目不同。'); return; } setAv(v.agent_version_id); setBv(v.benchmark_version_id); setProfile(v.profile_version_id); }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [api,projectId,source]);
  function body() { return mode === 'reuse' ? { request_key:requestKey,project_id:projectId,agent_version_id:av,benchmark_version_id:bv,profile_version_id:profile } : { request_key:requestKey,project_id:projectId || null,project_name:projectName,agent_name:name,adapter_type:adapter,agent:{ version,configuration:adapter === 'http' ? { endpoint,...(credential ? { credential_version_id:credential } : {}) } : { plugin_id:plugin },capabilities,effects:'unknown' },benchmark:{ version:'1.0',cases:parseCases(cases) },goals,environment_id:environment || null,execution }; }
  async function preflight() { setBusy(true); setError(''); try { setPreview(await api.post<Preview>(mode === 'reuse' ? 'onboarding/reuse-preview' : 'onboarding/preview',body())); } catch (e) { setPreview(null); setError((e as Error).message); } finally { setBusy(false); } }
  async function locate() {
    setBusy(true); setError(''); setQueried(false);
    try {
      const query = projectId ? `?${new URLSearchParams({ project_id: projectId })}` : '';
      const result = await api.get<{ found: boolean; run: Pick<Run, 'id' | 'project_id'> | null }>(`onboarding/requests/${encodeURIComponent(requestKey)}${query}`);
      if (result.found && result.run) { draft.clean(); await finished(result.run); }
      else { setQueried(true); setError('尚无已提交结果，原请求可能仍在途。可重新预检并用同一请求标识重试。'); }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function submit(retry = false) {
    if (busy || (retry ? !unknown || !queried : !preview)) return;
    setBusy(true); setError('');
    let submitted = false;
    try {
      const configuration = body();
      if (retry) await api.post(mode === 'reuse' ? 'onboarding/reuse-preview' : 'onboarding/preview', configuration);
      await draft.save({ ...payload, pending_submission: true });
      submitted = true;
      const run = await api.post<Run>(mode === 'reuse' ? 'onboarding/reuse' : 'onboarding/evaluations', configuration);
      draft.clean(); await finished(run);
    } catch (e) {
      setError((e as Error).message);
      if (submitted && (!(e instanceof APIError) || !e.status || e.status >= 500 || e.status === 409)) { setUnknown(true); setQueried(false); }
    } finally { setBusy(false); }
  }
  async function next() { if (step === 2) return submit(); setBusy(true); setError(''); try { if (step === 0) { if (!name.trim() || !version.trim()) throw new Error('请填写名称和版本名。'); if (adapter !== 'http' && !plugin) throw new Error('请选择部署插件。'); const check = await api.post<{ message:string }>('onboarding/check',{ project_id:projectId || null,adapter_type:adapter,configuration:adapter === 'http' ? { endpoint,...(credential ? { credential_version_id:credential } : {}) } : { plugin_id:plugin } }); setConnection(check.message); setStep(1); } else { if (!goals.length || goals.includes('state') && !environment) throw new Error('请选择目标及所需独立环境。'); setStep(2); } } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  function refundExample() { setCases(JSON.stringify([{ case_key:'refund-1001',input:{ order_id:'1001' },assertions:{ expected_output:{ order_id:'1001',status:'requested' },state:{ checks:[{ path:'/refunds',expected:[{ order_id:'1001',status:'requested' }] }] } } }],null,2)); change(); }
  return <><Heading title="三步开始评测" description="接入自己的 Agent，或复用固定版本执行回归。测试用例可在第三步导入，也可明确选择演示样例。"/>{draft.panel}<ErrorBox message={error}/><div className="button-group"><button className={`button ${mode === 'new' ? 'primary' : 'secondary'}`} disabled={unknown || busy} onClick={() => { setMode('new'); change(); }}>接入新 Agent</button><button className={`button ${mode === 'reuse' ? 'primary' : 'secondary'}`} disabled={!projectId || unknown || busy} onClick={() => { setMode('reuse'); change(); }}>评测已有版本</button></div>
  {unknown ? <section className="panel"><div className="form-body"><h2>提交结果尚未确认</h2><p>请求 {requestKey}。查询未找到已提交结果时，可重新预检并沿用请求标识。</p><div className="button-group"><button className="button primary" disabled={busy} onClick={() => void locate()}>查询原请求结果</button>{queried && <button className="button secondary" disabled={busy} onClick={() => void submit(true)}>按原请求重试</button>}<button className="button ghost" disabled={busy} onClick={() => { setUnknown(false); setPreview(null); }}>返回修改配置</button></div><p>原请求可能仍会完成。修改配置后提交会创建一项独立评测。</p></div></section> : <>
  {mode === 'new' && <ol className="guided-steps" aria-label="评测步骤">{['连接 Agent','选择评测目标','执行并分析'].map((label,i) => <li key={label} aria-current={step === i ? 'step' : undefined}><b>{i+1}</b>{label}</li>)}</ol>}
  <section className="panel"><div className="form-body guided-form"><fieldset disabled={busy} className="workflow-fields">
  {mode === 'reuse' ? <>
    <p>选择已发布的 Agent、Benchmark 和评分方案。执行参数由固定评分方案决定。</p>
    <PagedScopeChoice api={api} path={`quality/agent-versions?project_id=${projectId}`} label="复用 Agent 版本" value={av} changed={v => { setAv(v); change(); }} read={readAgents}/>
    <PagedScopeChoice api={api} path={`workspace-versions/${projectId}?project_id=${projectId}`} label="复用 Benchmark 版本" value={bv} changed={v => { setBv(v); change(); }} read={readBenchmarks}/>
    <PagedScopeChoice api={api} path={`profile-versions?project_id=${projectId}`} label="复用评分方案" value={profile} changed={v => { setProfile(v); change(); }} read={readProfiles}/>
  </> : <>
  {step === 0 && <>{!projectId && <label className="field"><span>项目名称</span><input value={projectName} onChange={e => { setProjectName(e.target.value); change(); }}/></label>}<div className="form-grid"><label className="field"><span>Agent 名称</span><input value={name} maxLength={120} onChange={e => { setName(e.target.value); change(); }}/></label><label className="field"><span>Agent 版本名</span><input value={version} maxLength={64} onChange={e => { setVersion(e.target.value); change(); }}/></label></div><label className="field"><span>接入方式</span><select value={adapter} onChange={e => { const type=e.target.value; setAdapter(type); setPlugin(''); setCapabilities(type === 'http' ? [] : ['tool_calls','workflow_steps','retrieval_results']); change(); }}><option value="python">Python 可信插件</option><option value="langgraph">LangGraph 工作流</option><option value="http">HTTP API</option></select></label>
  {adapter === 'http' ? <><label className="field"><span>Agent HTTP 地址</span><input type="url" value={endpoint} onChange={e => { setEndpoint(e.target.value); change(); }}/><small>仅 HEAD 检查，须配置精确允许清单。</small></label><CredentialSelector api={api} projectId={projectId} value={credential} changed={v => { setCredential(v); change(); }}/></> : <label className="field"><span>部署插件</span><select value={plugin} onChange={e => { setPlugin(e.target.value); change(); }}><option value="">请选择部署插件</option>{options[adapter as 'python'|'langgraph'].map(id => <option key={id}>{id}</option>)}</select></label>}
  <fieldset className="goal-options"><legend>声明的观测通道（首次执行核验）</legend>{[['tool_calls','工具调用'],['workflow_steps','工作流步骤'],['retrieval_results','检索结果']].map(([id,label]) => <label key={id}><input type="checkbox" checked={capabilities.includes(id)} onChange={e => { setCapabilities(c => e.target.checked ? [...c,id] : c.filter(x => x !== id)); change(); }}/>{label}</label>)}</fieldset></>}
  {step === 1 && <><div className="callout">{connection}</div><fieldset className="goal-options"><legend>希望验证什么？</legend>{goalsList.map(([id,label,cap]) => <label key={id}><input type="checkbox" checked={goals.includes(id)} disabled={Boolean(cap && !capabilities.includes(cap))} onChange={e => { setGoals(g => e.target.checked ? [...g,id] : g.filter(x => x !== id)); change(); }}/><span><strong>{label}</strong></span></label>)}</fieldset>{goals.includes('state') && <label className="field"><span>独立环境适配器</span><select value={environment} onChange={e => { setEnvironment(e.target.value); change(); }}><option value="">请选择</option>{options.environments.map(id => <option key={id}>{id}</option>)}</select></label>}</>}
  {step === 2 && <><div className="button-group"><label className="button secondary file-button">导入 JSON / JSONL<input type="file" accept=".json,.jsonl" onChange={async e => { const f=e.target.files?.[0]; if (!f) return; if (f.size > 1900000) return setError('文件需小于 1.9 MB。'); setCases(await f.text()); change(); }}/></label><button className="button ghost" onClick={() => { setCases(JSON.stringify(sampleCases,null,2)); change(); }}>填入订单演示用例</button><button className="button ghost" onClick={refundExample}>填入退款演示用例</button></div><label className="field"><span>测试用例</span><textarea className="code-input" rows={10} value={cases} onChange={e => { setCases(e.target.value); change(); }}/></label><CaseEditor text={cases} changed={v => { setCases(v); change(); }}/><div className="form-grid three">{[['repetitions','重复次数',1,10],['max_concurrency','执行并发',1,16],['timeout_seconds','执行超时（秒）',0.1,300]].map(([key,label,min,max]) => <label className="field" key={key}><span>{label}</span><input type="number" min={min} max={max} step={key === 'timeout_seconds' ? 0.1 : 1} value={execution[key as keyof typeof execution] as number} onChange={e => { setExecution(v => ({...v,[key]:Number(e.target.value)})); change(); }}/></label>)}</div></>}
  </>}</fieldset>
  {(mode === 'reuse' || step === 2) && <><button className="button secondary" disabled={busy} onClick={() => void preflight()}>预检并核对配置</button>{preview && <div className="callout"><div><h3>提交前配置确认</h3><p>{preview.agent_label} · {preview.benchmark_label} · {preview.profile_label}</p><p>有效用例 {preview.case_count} 条 · 计划 Trial {preview.planned_trials} 次 · 重复 {preview.execution.repetitions} · 并发 {preview.execution.max_concurrency} · 超时 {preview.execution.timeout_seconds}s</p><p>预计 Judge 初始调用 {preview.judge.initial_calls}，含重试最多 {preview.judge.possible_calls_with_retry}；预算 {preview.judge.budget}，并非费用估算。</p><details><summary>查看固定配置</summary><Code value={preview}/></details></div></div>}</>}
  <div className="button-group">{mode === 'new' && step > 0 && <button className="button secondary" disabled={busy} onClick={() => setStep(s => s-1)}>上一步</button>}<button className="button primary" disabled={busy || (mode === 'reuse' || step === 2) && !preview} onClick={() => void (mode === 'reuse' ? submit() : next())}>{busy ? '处理中…' : mode === 'reuse' ? '执行复用评测' : step === 0 ? '校验接入并继续' : step === 1 ? '下一步：准备测试用例' : '执行评测并查看分析'}<Icon name="arrow"/></button></div>
  </div></section></>}
  </>;
}
