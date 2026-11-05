export type JSONValue = null | boolean | number | string | JSONValue[] | { [key: string]: JSONValue };
export type RecordJSON = Record<string, JSONValue>;
export interface Entity { id: string; created_at: string }
export interface Project extends Entity { name: string; description: string }
export interface Agent extends Entity { project_id: string; name: string; description: string; adapter_type: string }
export interface AgentVersion extends Entity { agent_id: string; version: string; capabilities: string[]; configuration: RecordJSON; content_hash: string }
export interface Benchmark extends Entity { project_id: string; name: string; description: string }
export interface BenchmarkVersion extends Entity { benchmark_name?: string; benchmark_id: string; version: string; case_count: number; content_hash: string }
export interface EvaluatorSpec { instance_id: string; id: string; version?: string; required?: boolean; hard_constraint?: boolean; config?: RecordJSON }
export interface Profile extends Entity { project_id: string; name: string; version: string; content_hash: string; definition: { evaluators: EvaluatorSpec[]; execution: { repetitions: number; timeout_seconds: number; max_concurrency: number }; gate: { min_success_rate: number; max_success_rate_drop: number; min_decision_coverage: number; core_case_keys: string[] } } }
export interface QualitySummary { grading_batch_id: string | null; revision: number | null; grading_status: string | null; quality_status: string; counts: Record<string, number>; planned_trials: number; unassessed_trials: number; trial_scoring_coverage: number | null; updated_at: string | null; duration_ms: number | null; initiator: { id: string | null; display_name: string; kind: string } }
export interface Run extends Entity { agent_name?: string; agent_version?: string; benchmark_name?: string; benchmark_version?: string; quality_summary?: QualitySummary; project_id: string; agent_version_id: string; benchmark_version_id: string; profile_version_id: string; status: string; manifest_hash: string; manifest: { agent: { adapter_type: string; configuration: RecordJSON }; profile: Profile['definition'] }; grading_batches?: Batch[] }
export interface Batch extends Entity { run_id: string; revision: number; status: string }
export interface Trial { trial_id: string; case_key: string; repetition_index: number; verdict: string; reasons: string[]; execution_id: string; execution_status: string }
export interface Report { quality_summary?: QualitySummary; trial_scoring_coverage?: number; specialty_metrics?: SpecialtyMetrics; run_id: string; grading_batch_id: string; run_status: string; grading_status: string; planned_trials: number; counts: Record<string, number>; success_rate: number | null; decision_coverage: number; confirmed_success_fraction: number; scoring_coverage: number; latency_ms: { p50: number | null; p95: number | null; samples: number; min_samples?: number; sufficient?: boolean; scope?: string; excluded_trials?: number }; trials: Trial[]; trials_total?: number; hard_constraint_violations: number }
export interface Execution extends Entity { environment_evidence?: RecordJSON | null; trial_id: string; trace_id: string; status: string; response: RecordJSON | null; error: RecordJSON | null; latency_ms: number | null }
export interface Score extends Entity { execution_id: string; instance_id: string; result: { evaluator_id: string; grading_status: string; passed: boolean | null; score: number | null; explanation: string; evidence: RecordJSON; required: boolean; hard_constraint: boolean } }
export interface ComparisonSide { agent_id: string; agent_name: string; agent_version: string; benchmark_name: string; benchmark_version: string; run_id: string; run_created_at: string; batch_id: string; batch_revision: number; batch_created_at: string }
export interface Comparison extends Entity { context?: { baseline: ComparisonSide | null; candidate: ComparisonSide | null }; report: { metric_deltas?: Record<string, { baseline: number | null; candidate: number | null; delta: number | null }>; baseline_batch_id: string; candidate_batch_id: string; mode: string; compatible: boolean; differences: string[]; baseline: Report; candidate: Report; success_rate_delta: number | null; new_failures: string[]; fixed_cases: string[]; uncertain_cases: string[]; gate: { status: string; reasons: string[] } } }
export interface Page<T> { items: T[]; total?: number; offset?: number; limit?: number }


export interface WorkItem extends Entity { kind: string; target_id: string; status: string; epoch: number; delivery_count: number; last_error: string | null; lease_until: number }

export interface SpecialtyMetrics {
  granularities: Record<string, { evaluators: { instance_id: string; evaluator_id: string; metadata: { quality_dimension: string; scoring_method: string }; mean_score: number | null; pass_rate: number | null; coverage: number; scored: number; expected: number }[] }>;
  state: { success_rate: number | null; coverage: number; validation_scope: string; reset_failures: number };
  tool: { error_rate: number | null; samples: number };
  trajectory: { loop_rate: number | null; recovery_rate: number | null };
  reliability: { timeout_rate: number | null; repeated_run_success: number | null };
  usage: { agent_tokens_per_task: number | null; agent_token_coverage: number };
}
