import benchmark from '../../benchmarks/orders.json';
import type { API } from './api';
import type { Agent, AgentVersion, Benchmark, BenchmarkVersion, Comparison, Profile, Project, Run } from './types';

export const sampleCases = benchmark.cases;
export const evaluators = [
  { instance_id: 'answer', id: 'exact_match', required: true },
  { instance_id: 'tools', id: 'tool_selection', required: true, hard_constraint: true, config: { forbidden: ['delete_order'] } },
  { instance_id: 'arguments', id: 'tool_arguments', required: true },
  { instance_id: 'steps', id: 'trajectory', required: true },
  { instance_id: 'retrieval', id: 'rag', required: true, config: { metric: 'recall', k: 5 } },
];
export const caps = ['tool_calls', 'workflow_steps', 'retrieval_results'];

export async function seedExample(api: API): Promise<Project> {
  const project = await api.post<Project>('projects', { name: '订单 Agent 评测', description: '本地示例 · 验证版本回归与评分证据，非真实业务基准。' });
  const agent = await api.post<Agent>('agents', { project_id: project.id, name: 'Order Assistant', adapter_type: 'python', description: '查询订单状态的可信 Python 示例' });
  const versions: AgentVersion[] = [];
  for (const n of [1, 2]) versions.push(await api.post<AgentVersion>(`agents/${agent.id}/versions`, { version: `${n}.0`, configuration: { plugin_id: `example.order.v${n}` }, capabilities: caps }));
  const benchmark = await api.post<Benchmark>('benchmarks', { project_id: project.id, name: '订单查询 Golden Cases', description: '两条订单用例，覆盖答案、工具、轨迹和检索。' });
  const version = await api.post<BenchmarkVersion>(`benchmarks/${benchmark.id}/versions`, { version: '1.0', cases: sampleCases });
  const profile = await api.post<Profile>('profile-versions', { project_id: project.id, name: '订单质量评测', version: '1.0', evaluators, gate: { core_case_keys: ['order-1001'] } });
  const runs: Run[] = [];
  for (const agentVersion of versions) runs.push(await api.post<Run>('evaluations', { agent_version_id: agentVersion.id, benchmark_version_id: version.id, profile_version_id: profile.id }));
  const batches: string[] = [];
  for (const run of runs) {
    const deadline = Date.now() + 60000;
    while (true) {
      const state = await api.get<Run>(`evaluations/${run.id}`);
      if (state.status === 'completed') { batches.push(state.grading_batches![0].id); break; }
      if (['failed', 'cancelled'].includes(state.status)) throw new Error('示例执行未完成，请查看评测任务。');
      if (Date.now() >= deadline) throw new Error('示例执行超过等待时间，可在任务列表继续查看。');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
  await api.post<Comparison>('regressions/compare', { baseline_batch_id: batches[0], candidate_batch_id: batches[1] });
  return project;
}

