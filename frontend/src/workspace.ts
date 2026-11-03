import { useCallback, useEffect, useRef, useState } from 'react';
import type { API } from './api';
import type { Agent, AgentVersion, Benchmark, BenchmarkVersion, Comparison, Profile, Run } from './types';

export interface Workspace {
  agents: Agent[]; agentVersions: AgentVersion[]; benchmarks: Benchmark[];
  benchmarkVersions: BenchmarkVersion[]; profiles: Profile[]; runs: Run[]; comparisons: Comparison[];
  versions_truncated?: boolean; totals?: Record<string, number>; offsets?: Record<string, number>; limit?: number;
  agent_names?: Record<string, string>; benchmark_names?: Record<string, string>;
}
const empty: Workspace = { agents: [], agentVersions: [], benchmarks: [], benchmarkVersions: [], profiles: [], runs: [], comparisons: [] };

export function useWorkspace(api: API, projectId: string) {
  const [data, setData] = useState<Workspace>(empty);
  const [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [pages, setPages] = useState<Record<string, number>>({});
  const generation = useRef(0);
  const reload = useCallback(async () => {
    const epoch = ++generation.current;
    if (!projectId) { setData(empty); return; }
    setLoading(true); setError('');
    try {
      const query = new URLSearchParams({ limit: '10' });
      for (const [key, value] of Object.entries(pages)) query.set(`${key}_offset`, String(value * 10));
      const response = await api.get<Workspace>(`workspace/${projectId}?${query}`);
      if (generation.current === epoch) setData(response);
    } catch (e) { if (generation.current === epoch) setError((e as Error).message); }
    finally { if (generation.current === epoch) setLoading(false); }
  }, [api, projectId, pages]);
  useEffect(() => { setPages({}); }, [projectId]);
  useEffect(() => { setData(empty); void reload(); return () => { generation.current++; }; }, [reload]);
  const active = (data.totals?.active_runs || 0) > 0;
  useEffect(() => { if (!active) return; const timer = setInterval(() => void reload(), 2500); return () => clearInterval(timer); }, [active, reload]);
  return { data, loading, error, reload, pages, setPage: (key: string, page: number) => setPages(s => ({ ...s, [key]: page })) };
}

export function agentName(data: Workspace, versionId: string) {
  const version = data.agentVersions.find(v => v.id === versionId);
  return `${data.agent_names?.[version?.agent_id || ''] || data.agents.find(a => a.id === version?.agent_id)?.name || 'Agent'}${version ? ` · v${version.version}` : ''}`;
}
export function benchmarkName(data: Workspace, versionId: string) {
  const version = data.benchmarkVersions.find(v => v.id === versionId);
  return `${data.benchmark_names?.[version?.benchmark_id || ''] || data.benchmarks.find(b => b.id === version?.benchmark_id)?.name || 'Benchmark'}${version ? ` · v${version.version}` : ''}`;
}
