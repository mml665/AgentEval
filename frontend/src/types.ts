export type JSONValue = null | boolean | number | string | JSONValue[] | { [key: string]: JSONValue };
export type RecordJSON = Record<string, JSONValue>;
export interface Entity { id: string; created_at: string }
export interface Project extends Entity { name: string; description: string }
export interface Agent extends Entity { project_id: string; name: string; description: string; adapter_type: string }
export interface AgentVersion extends Entity { agent_id: string; version: string; capabilities: string[]; configuration: RecordJSON; content_hash: string }
export interface Benchmark extends Entity { project_id: string; name: string; description: string }
export interface BenchmarkVersion extends Entity { benchmark_id: string; version: string; case_count: number; content_hash: string }
export interface EvaluatorSpec { instance_id: string; id: string; version?: string; required?: boolean; hard_constraint?: boolean; config?: RecordJSON }
export interface Profile extends Entity { project_id: string; name: string; version: string; content_hash: string; definition: { evaluators: EvaluatorSpec[]; execution: { repetitions: number; timeout_seconds: number; max_concurrency: number }; gate: { min_success_rate: number; max_success_rate_drop: number; min_decision_coverage: number; core_case_keys: string[] } } }
export interface Run extends Entity { project_id: string; agent_version_id: string; benchmark_version_id: string; profile_version_id: string; status: string; manifest_hash: string; manifest: { agent: { adapter_type: string; configuration: RecordJSON }; profile: Profile['definition'] }; grading_batches?: Batch[] }
export interface Batch extends Entity { run_id: string; revision: number; status: string }
export interface Trial { trial_id: string; case_key: string; repetition_index: number; verdict: string; reasons: string[]; execution_id: string; execution_status: string }
export interface Report { run_id: string; grading_batch_id: string; run_status: string; grading_status: string; planned_trials: number; counts: Record<string, number>; success_rate: number | null; decision_coverage: number; confirmed_success_fraction: number; scoring_coverage: number; latency_ms: { p50: number | null; p95: number | null; samples: number }; trials: Trial[]; hard_constraint_violations: number }
export interface Execution extends Entity { trial_id: string; trace_id: string; status: string; response: RecordJSON | null; error: RecordJSON | null; latency_ms: number | null }
export interface Score extends Entity { execution_id: string; instance_id: string; result: { evaluator_id: string; grading_status: string; passed: boolean | null; score: number | null; explanation: string; evidence: RecordJSON; required: boolean; hard_constraint: boolean } }
export interface Comparison extends Entity { report: { baseline_batch_id: string; candidate_batch_id: string; mode: string; compatible: boolean; differences: string[]; baseline: Report; candidate: Report; success_rate_delta: number | null; new_failures: string[]; fixed_cases: string[]; uncertain_cases: string[]; gate: { status: string; reasons: string[] } } }
export interface Page<T> { items: T[]; total?: number; offset?: number; limit?: number }

