import { useCallback, useEffect, useRef, useState } from 'react';
import type { API } from './api';
import type { Agent, AgentVersion, Benchmark, BenchmarkVersion, Comparison, Profile, Run } from './types';

export interface Workspace {
  agents: Agent[]; agentVersions: AgentVersion[]; benchmarks: Benchmark[];
  benchmarkVersions: BenchmarkVersion[]; profiles: Profile[]; runs: Run[]; comparisons: Comparison[];
}
const empty: Workspace = { agents: [], agentVersions: [], benchmarks: [], benchmarkVersions: [], profiles: [], runs: [], comparisons: [] };

export function useWorkspace(api: API, projectId: string) {
  const [data, setData] = useState<Workspace>(empty);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const reload = useCallback(async () => {
    const epoch = ++generation.current;
    if (!projectId) { setData(empty); return; }
    setLoading(true); setError('');
    try {
      const query = `?project_id=${projectId}`;
      const [agents, benchmarks, profiles, runs, comparisons] = await Promise.all([
        api.all<Agent>('agents' + query), api.all<Benchmark>('benchmarks' + query),
        api.all<Profile>('profile-versions' + query), api.all<Run>('evaluations' + query), api.all<Comparison>('regressions'),
      ]);
      const [av, bv] = await Promise.all([
        Promise.all(agents.map(a => api.get<{ items: AgentVersion[] }>(`agents/${a.id}/versions`))),
        Promise.all(benchmarks.map(b => api.get<{ items: BenchmarkVersion[] }>(`benchmarks/${b.id}/versions`))),
      ]);
      const ids = new Set(runs.map(r => r.id));
      if (generation.current === epoch) setData({ agents, benchmarks, profiles, runs,
        comparisons: comparisons.filter(c => ids.has(c.report.baseline.run_id)),
        agentVersions: av.flatMap(x => x.items), benchmarkVersions: bv.flatMap(x => x.items) });
    } catch (e) { if (generation.current === epoch) setError((e as Error).message); }
    finally { if (generation.current === epoch) setLoading(false); }
  }, [api, projectId]);
  useEffect(() => { setData(empty); void reload(); return () => { generation.current++; }; }, [reload]);
  const active = data.runs.some(r => !['completed', 'failed', 'cancelled'].includes(r.status));
  useEffect(() => { if (!active) return; const timer = setInterval(() => void reload(), 2500); return () => clearInterval(timer); }, [active, reload]);
  return { data, loading, error, reload };
}

export function agentName(data: Workspace, versionId: string) {
  const version = data.agentVersions.find(v => v.id === versionId);
  return `${data.agents.find(a => a.id === version?.agent_id)?.name || 'Agent'}${version ? ` · v${version.version}` : ''}`;
}
export function benchmarkName(data: Workspace, versionId: string) {
  const version = data.benchmarkVersions.find(v => v.id === versionId);
  return `${data.benchmarks.find(b => b.id === version?.benchmark_id)?.name || 'Benchmark'}${version ? ` · v${version.version}` : ''}`;
}
