import { useEffect, useState } from 'react';
import type { API } from './api';
import type { Run } from './types';
import { sampleCases } from './sample';
import { ErrorBox, Heading, Icon, Code } from './ui';

interface Options { python: string[]; langgraph: string[]; environments: string[] }
const goalOptions = [
  ['answer', '回答正确性', '', '对照 expected_output 检查最终回答。'],
  ['tools', '工具调用', 'tool_calls', '逐调用核验参数、结果和依赖。'],
  ['trajectory', '执行轨迹', 'workflow_steps', '检查步骤、禁止行为及路径规则。'],
  ['retrieval', 'RAG 检索', 'retrieval_results', '使用人工相关性标签计算 Recall / MRR / NDCG@5。'],
  ['state', '独立状态验证', '', '通过部署好的环境适配器核验实际状态。'],
];

export function GuidedEvaluation({ api, projectId, finished }: { api: API; projectId: string; finished: (run: Run) => Promise<void> }) {
  const [step, setStep] = useState(0), [options, setOptions] = useState<Options>({ python: [], langgraph: [], environments: [] });
  const [adapter, setAdapter] = useState('python'), [plugin, setPlugin] = useState('example.order.v1'), [endpoint, setEndpoint] = useState('');
  const [name, setName] = useState('我的 Agent'), [projectName, setProjectName] = useState('我的质量项目');
  const [capabilities, setCapabilities] = useState<string[]>(['tool_calls', 'workflow_steps', 'retrieval_results']);
  const [goals, setGoals] = useState<string[]>(['answer']), [environment, setEnvironment] = useState('');
  const [cases, setCases] = useState(JSON.stringify(sampleCases, null, 2)), [error, setError] = useState(''), [busy, setBusy] = useState(false), [connection, setConnection] = useState('');
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  useEffect(() => { void api.get<Options>('onboarding/options').then(setOptions).catch(e => setError(e.message)); }, [api]);
  function change() { setRequestKey(crypto.randomUUID()); setError(''); }
  async function next() {
    setError(''); setBusy(true);
    try {
      if (step === 0) {
        if (!name.trim() || (!projectId && !projectName.trim())) throw new Error('请填写项目和 Agent 名称。');
        const check = await api.post<{ message: string }>('onboarding/check', { adapter_type: adapter, configuration: adapter === 'http' ? { endpoint } : { plugin_id: plugin } });
        setConnection(check.message); setStep(1);
      } else if (step === 1) {
        if (!goals.length) throw new Error('至少选择一个评测目标。');
        if (goals.includes('state') && !environment) throw new Error('请选择独立环境适配器。');
        if (goalOptions.some(([id, , cap]) => goals.includes(id) && cap && !capabilities.includes(cap))) throw new Error('当前声明能力不支持所选目标，请返回接入步骤检查。');
        setStep(2);
      } else {
        let parsed;
        try { parsed = JSON.parse(cases); } catch {
          parsed = cases.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
        }
        const list = Array.isArray(parsed) ? parsed : parsed.cases;
        if (!Array.isArray(list) || !list.length) throw new Error('需要非空测试用例，包含 case_key、input、assertions。');
        const run = await api.post<Run>('onboarding/evaluations', {
          request_key: requestKey, project_id: projectId || null, project_name: projectName, agent_name: name, adapter_type: adapter,
          agent: { version: '1.0', configuration: adapter === 'http' ? { endpoint } : { plugin_id: plugin }, capabilities, effects: 'unknown' },
          benchmark: { version: '1.0', cases: list }, goals, environment_id: environment || null,
        });
        await finished(run);
      }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function refundExample() {
    setCases(JSON.stringify([{ case_key: 'refund-1001', input: { order_id: '1001' }, assertions: {
      expected_output: { order_id: '1001', status: 'requested' }, state: { checks: [{ path: '/refunds', expected: [{ order_id: '1001', status: 'requested' }] }] },
    } }], null, 2)); change();
  }
  return <><Heading eyebrow="FIRST EFFECTIVE EVALUATION" title="三步开始评测" description="连接 Agent、选择要验证的目标，再导入用例执行。平台会保存固定版本与证据。"/>
    <ol className="guided-steps" aria-label="评测步骤">{['连接 Agent', '选择评测目标', '执行并分析'].map((label, i) => <li key={label} aria-current={step === i ? 'step' : undefined}><b>{i + 1}</b>{label}</li>)}</ol>
    <section className="panel"><div className="form-body guided-form"><ErrorBox message={error}/>
      {step === 0 && <>{!projectId && <label className="field"><span>项目名称</span><input value={projectName} maxLength={120} onChange={e => { setProjectName(e.target.value); change(); }}/></label>}
        <label className="field"><span>Agent 名称</span><input value={name} maxLength={120} onChange={e => { setName(e.target.value); change(); }}/></label>
        <label className="field"><span>接入方式</span><select value={adapter} onChange={e => { const type = e.target.value; setAdapter(type); setPlugin(type === 'langgraph' ? 'example.order.graph' : 'example.order.v1'); setCapabilities(type === 'http' ? [] : ['tool_calls', 'workflow_steps', 'retrieval_results']); change(); }}><option value="python">Python 可信插件</option><option value="langgraph">LangGraph 工作流</option><option value="http">HTTP API</option></select></label>
        {adapter === 'http' ? <label className="field"><span>Agent HTTP 地址</span><input type="url" value={endpoint} onChange={e => { setEndpoint(e.target.value); change(); }} placeholder="http://127.0.0.1:9001/invoke"/><small>需在服务端配置精确主机:端口允许清单。检查只发送 HEAD，不发送业务执行 POST。</small></label> : <label className="field"><span>部署插件</span><select value={plugin} onChange={e => { setPlugin(e.target.value); change(); }}>{options[adapter as 'python' | 'langgraph'].map(id => <option key={id}>{id}</option>)}</select></label>}
        <fieldset className="goal-options"><legend>声明的观测通道（首次执行核验）</legend>{[['tool_calls', '工具调用'], ['workflow_steps', '工作流步骤'], ['retrieval_results', '检索结果']].map(([id, label]) => <label key={id}><input type="checkbox" checked={capabilities.includes(id)} onChange={e => { setCapabilities(c => e.target.checked ? [...c, id] : c.filter(x => x !== id)); change(); }}/>{label}</label>)}</fieldset></>}
      {step === 1 && <><div className="callout">{connection}</div><fieldset className="goal-options"><legend>希望验证什么？</legend>{goalOptions.map(([id, label, cap, description]) => <label key={id}><input type="checkbox" checked={goals.includes(id)} disabled={Boolean(cap && !capabilities.includes(cap))} onChange={e => { setGoals(g => e.target.checked ? [...g, id] : g.filter(x => x !== id)); change(); }}/><span><strong>{label}</strong><small>{description}{cap && !capabilities.includes(cap) && ' · 当前未声明所需观测能力'}</small></span></label>)}</fieldset>
        {goals.includes('state') && <label className="field"><span>独立环境适配器</span><select value={environment} onChange={e => { setEnvironment(e.target.value); change(); }}><option value="">选择已部署环境</option>{options.environments.map(id => <option key={id}>{id}</option>)}</select><small>当前退款样例使用隔离 SQLite；业务环境需部署自己的适配器。</small></label>}</>}
      {step === 2 && <><p>将执行 {name} 的新评测。服务端会检查用例标签和所选目标是否兼容。</p><div className="button-group"><label className="button secondary file-button">导入 JSON / JSONL<input type="file" accept=".json,.jsonl" onChange={async e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 1900000) { setError('文件需小于 1.9 MB。'); return; } setCases(await file.text()); change(); }}/></label><button className="button ghost" onClick={() => { setCases(JSON.stringify(sampleCases, null, 2)); change(); }}>订单查询示例</button><button className="button ghost" onClick={refundExample}>退款状态示例</button></div>
        <label className="field"><span>测试用例</span><textarea className="code-input" rows={14} value={cases} onChange={e => { setCases(e.target.value); change(); }}/><small>请自行确认预期结果；不会自动生成或猜测业务正确性标签。</small></label>
        <details><summary>查看自动生成配置说明</summary><Code value={{ goals, capabilities, environment: environment || null, version_policy: '固定版本；新接入不会覆盖历史资源' }}/></details></>}
      <div className="button-group">{step > 0 && <button className="button secondary" disabled={busy} onClick={() => { setError(''); setStep(s => s - 1); }}>上一步</button>}<button className="button primary" disabled={busy} onClick={() => void next()}>{busy ? '处理中…' : step === 0 ? '校验接入并继续' : step === 1 ? '下一步：准备测试用例' : '执行评测并查看分析'}<Icon name="arrow"/></button></div>
    </div></section></>;
}
