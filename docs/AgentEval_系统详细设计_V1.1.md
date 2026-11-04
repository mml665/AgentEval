# AgentEval 系统详细设计文档

**文档版本：** V1.1  
**对应产品版本：** MVP V1.0  
**状态：** 目标设计基线；M1 实现范围与验证状态见 [构建状态](BUILD_STATUS.md)  
**修订日期：** 2026-11-04
**需求依据：** [项目 PRD](AgentEval_PRD_V1.1.md)

## 1. 总体架构与部署边界

系统采用模块化单体 API 加独立 Worker。MVP 为单机、单租户、可信团队部署；Project 用于组织数据，不构成权限隔离。后续多租户需要独立安全设计与迁移。

```text
React Web / Python SDK
          |
      FastAPI API ───── 凭据引用 / 访问策略 / 审计
          |
     PostgreSQL（版本、Manifest、状态、结果、Outbox）
          |
     Outbox Dispatcher → Redis / Celery → Worker
                                           |
                         Adapter → 被测 Agent / 受控环境
                                           |
                         Trace Collector → Evaluator Registry
                                           |
                         Aggregator → Bad Case → Regression
```

数据库是任务及结果事实来源，Redis 负责投递和短期协调，不作为唯一状态存储。队列按可能重复投递设计，不假设端到端恰好执行一次。

| 层次 | 职责 | 主要模块 |
|---|---|---|
| 表现层 | 管理配置、查看进度与证据 | React、TypeScript |
| 控制层 | 校验、发布版本、创建与查询任务 | FastAPI、Pydantic、鉴权、审计 |
| 接入层 | 调用、能力声明、输出标准化 | HTTP/Python/LangGraph Adapter |
| 执行层 | 逻辑试验、租约、超时、恢复 | Orchestrator、Scheduler、Worker |
| 评分层 | 输入检查、独立评分与重评 | Registry、规则、Judge |
| 分析层 | 指标聚合、失败标签、版本比较 | Aggregator、Regression、Report |

技术基线：Python 3.11+、FastAPI、SQLAlchemy、PostgreSQL、Celery、Redis、OpenTelemetry、pytest、React/TypeScript、Docker Compose。具体依赖版本在实现时锁定并验证，不在设计阶段假设任意最新版兼容。

## 2. 关键设计决策

1. Adapter 只负责调用与标准化，不嵌入业务评分。
2. 粒度、质量维度和评分机制分别建模，由 Profile 组合。
3. 发布后的 BenchmarkVersion、AgentVersion、ProfileVersion 不可变。
4. 执行、评分、任务判定、门禁相互独立，所有未知情况保留原因。
5. 重复试验、失败重试和评分重跑分别建模。
6. 版本比较先检查可比性，再计算变化和门禁。
7. Python 插件仅来自可信部署；不允许用户提交任意模块路径执行。
8. 真实外部副作用无法仅依靠本地事务保证恰好一次；通过幂等键、重置协议和未知结果处理控制风险。

## 3. 领域模型与身份规则

所有业务实体使用服务端生成的 UUID；时间统一存 UTC，API 返回带时区时间。可变配置有 revision 用于乐观并发控制；发布版本不可更新。

| 实体 | 关键字段 | 约束 |
|---|---|---|
| Project | id、name、description、owner | MVP 为逻辑归属 |
| AgentDefinition | id、project_id、name、adapter_type、owner | 不保存可变版本执行配置 |
| AgentVersion | id、agent_id、version、configuration、capabilities、code_ref、content_hash | `(agent_id, version)` 唯一；发布后不可变 |
| Benchmark | id、project_id、name、task_type、description | 逻辑数据集 |
| BenchmarkVersion | id、benchmark_id、version、schema_version、content_hash、case_count | `(benchmark_id, version)` 唯一 |
| TestCase | id、benchmark_version_id、case_key、input、assertions、tags、environment_config | `(benchmark_version_id, case_key)` 唯一 |
| ProfileVersion | id、project_id、profile_key、version、definition、content_hash | 包含执行、判定、门禁协议 |
| EvaluationRun | id、project_id、manifest、manifest_hash、status、progress、cancel_requested_at | 快照在入队前冻结 |
| Trial | id、run_id、case_id、repetition_index | `(run_id, case_id, repetition_index)` 唯一；不保存跨评分批次共用的判定 |
| CaseExecution | id、trial_id、attempt_index、status、error_type、output_ref、trace_id、lease_epoch | `(trial_id, attempt_index)` 唯一 |
| GradingBatch | id、run_id、revision、profile_hash、grading_manifest、status | 冻结评分配置与证据集；重评产生新批次 |
| TrialAssessment | id、trial_id、grading_batch_id、selected_execution_id、verdict、reasons | `(trial_id, grading_batch_id)` 唯一；历史批次判定互不覆盖 |
| EvaluationResult | id、grading_batch_id、execution_id、evaluator_id、evaluator_version、status、score、passed、evidence_ref | 同一批次/执行/评分器配置唯一 |
| TraceSpan/Event | id、execution_id、trace_id、span_id、event_id、payload_ref | Span 与事件分别存储或明确类型 |
| BadCase | id、trial_id、result_id、category、basis、confidence、evidence_ref | 可多标签，basis 区分事实/推断 |
| ReviewRecord | id、target_result_id、label、reviewer、reason、source、created_at | 追加写，不覆盖结果 |
| RegressionReport | id、baseline_batch_id、candidate_batch_id、mode、compatibility、metrics、gate、report_hash | 引用固定评分批次 |
| Artifact | id、project_id、path、size、checksum、redaction、retention_class | 有访问及留存策略 |
| Outbox / AuditEvent | id、event_type、payload、state / actor、action、target | 投递可靠性与审计 |

`case_key` 是 Benchmark 内逻辑身份；TestCase.id 是不可变版本中的记录身份。严格回归使用同一 BenchmarkVersion，因此按相同用例记录配对。跨版本探索比较可用 case_key 辅助映射，但必须展示输入和断言是否改变。

### 3.1 Benchmark 导入与发布

1. 限制文件大小、编码和用例数量，拒绝重复 JSON 键及非有限数值。
2. 校验 Schema、case_key 唯一性、断言类型、相关性标签、环境配置引用。
3. 返回行号/字段路径/错误码；整批发布原子提交，不部分成功。
4. 规范化 JSON：对象键排序、固定 UTF-8 编码和序列化规则；用例按 case_key 排序，保留每个用例内部数组顺序。
5. 对包含 Schema 版本、输入、断言、标签和环境配置的完整规范内容计算 SHA-256。格式变化不改变哈希，语义内容改变产生新哈希。
6. 创建 BenchmarkVersion 与 TestCase；后续修改必须发布新版本。

缺省值与显式 null 的规范化规则随 Schema 版本固定。导入预览结果不等于已发布版本。

### 3.2 标准用例示例

```json
{
  "schema_version": "1.0",
  "case_key": "order-1001",
  "input": {"message": "查询订单1001的状态及对应规则"},
  "assertions": {
    "expected_output": {"message": "订单1001已发货"},
    "tools": {
      "allowed": ["get_order", "search_policy"],
      "required": ["get_order"],
      "arguments": {"get_order": {"order_id": "1001"}}
    },
    "trajectory": {
      "required_steps": ["lookup", "respond"],
      "dependencies": [["lookup", "respond"]]
    },
    "retrieval": {
      "judgment_status": "complete",
      "relevance": {"policy-shipped": 2, "policy-refund": 0},
      "k": 5
    }
  },
  "tags": ["golden", "order"],
  "environment_config": {"fixture_ref": "orders-readonly-v1"}
}
```

字段命名与评分器配置必须共同进行 Schema 校验。示例预期输出采用严格匹配；实际开放式问答可换为结构化断言或 Judge，不隐式改变 Exact Match 行为。`judgment_status=complete` 表示标注方确认相关文档集合可用于该数据集指标，不意味着平台自动证明标注完整。

## 4. 标准契约与 Adapter

### 4.1 能力声明

```json
{
  "schema_version": "1.0",
  "observability": {
    "tool_calls": true,
    "workflow_steps": true,
    "retrieval_results": false,
    "final_state": false,
    "token_usage": true
  },
  "lifecycle": {
    "cancel": "cooperative",
    "reset": false,
    "idempotency_key": true
  },
  "effects": "read_only"
}
```

effects 为 `read_only`、`idempotent`、`stateful` 或 `unknown`；cancel 为 `none`、`cooperative` 或 `process_terminate`。由管理员配置可信声明，运行时再核验，不能仅凭 Agent 自报认定安全重试。

### 4.2 Adapter 接口

```python
class AgentAdapter(Protocol):
    async def invoke(self, request: InvocationRequest) -> InvocationResult: ...
    async def get_capabilities(self) -> Capabilities: ...
    async def health_check(self) -> HealthResult: ...
    async def cancel(self, invocation_id: str) -> CancellationResult: ...
    async def reset(self, environment: EnvironmentHandle) -> ResetResult: ...
```

不支持的 cancel/reset 返回结构化 `unsupported`，不能假装成功。invoke 的执行结果与调用取消确认分开保存。

### 4.3 标准输入

```json
{
  "schema_version": "1.0",
  "run_id": "uuid",
  "trial_id": "uuid",
  "execution_id": "uuid",
  "invocation_id": "uuid",
  "idempotency_key": "stable-key-for-this-logical-operation",
  "case_key": "order-1001",
  "session_id": "isolated-session-id",
  "input": {"message": "查询订单1001状态"},
  "context": {},
  "deadline_at": "2026-11-03T08:00:30Z",
  "trace_enabled": true
}
```

不同 repetition 默认使用隔离会话；一次试验内的网络恢复若依赖远端幂等协议则保持相同幂等键。通过环境重置重新执行时使用新的操作键并保留重置凭证，避免命中旧操作缓存。禁止将参考答案默认传给被测 Agent。

### 4.4 标准输出

```json
{
  "schema_version": "1.0",
  "invocation_id": "uuid",
  "status": "completed",
  "output": {"message": "订单1001已发货"},
  "artifacts": [],
  "trace_id": "32-hex-trace-id",
  "trace_completeness": "complete",
  "usage": {"input_tokens": 500, "output_tokens": 100, "source": "provider"},
  "final_state": null,
  "retrieval_results": null
}
```

usage 缺失为 null；禁止补零。Trace 完整度为 complete/partial/unavailable，按观测通道分别记录也可。状态数据必须标明来源是独立环境探针还是 Agent 自报。

### 4.5 各类适配

- **HTTP：** 固定请求/响应 Schema，连接与读取超时，响应大小上限；网络策略校验后调用。健康检查使用配置的无副作用路径。
- **Python：** plugin_id 映射到预部署 callable；每次调用在受控子进程中运行，通过 IPC 输出结果与事件，父进程可终止进程组。进程隔离仅服务于生命周期管理，不构成不可信代码沙箱。
- **LangGraph：** 基于已锁定版本映射工作流事件到标准步骤/工具事件；图对象在子进程内创建。示例必须验证步骤、父子关系与最终输出，不保证全部第三方事件自动兼容。

## 5. Evaluation Profile 与评分契约

```yaml
profile_key: order-agent
version: "1.0"
schema_version: "1.0"
evaluators:
  - instance_id: answer
    id: exact_match
    version: "1.0"
    granularity: end_to_end
    dimension: correctness
    required: true
    hard_constraint: false
    threshold: 1.0
    weight: 1.0
    config: {actual_path: "output.message", expected_path: "assertions.expected_output.message"}
  - instance_id: forbidden_tools
    id: forbidden_action
    version: "1.0"
    granularity: trajectory
    dimension: safety
    required: true
    hard_constraint: true
    config: {forbidden_tools: [delete_order]}
execution:
  repetitions: 3
  timeout_seconds: 60
  grading_timeout_seconds: 30
  max_concurrency: 4
  max_retries: 0
  retryable_errors: []
  retry_safety: disabled
verdict:
  required_policy: all
  missing_required: unknown
  retry_selection: last_authorized_attempt
gate:
  min_decision_coverage: 1.0
  min_success_rate: 0.95
  max_success_rate_drop: 0.02
  max_safety_violations: 0
  core_case_keys: [order-1001]
```

阈值仅作示例，不作为跨业务默认质量标准。硬约束自动为必需项；权重不能抵消硬约束。MVP 只支持必需项全部满足的判定，未来扩展其他组合策略需新协议版本。

### 5.1 Evaluator Registry

评分器定义包括 id、implementation_version、config_schema、required_inputs、output_schema、适用粒度与指标定义。相同实现版本不能静默替换代码；部署时记录制品摘要。

创建 Run 时检查：版本存在、配置合法、能力可满足、每个用例包含断言和相关性数据。显式选择的适用范围内，缺失必需输入直接拒绝创建；运行时未兑现观测则产生 missing_evidence。

```json
{
  "instance_id": "answer",
  "evaluator_id": "exact_match",
  "evaluator_version": "1.0",
  "grading_status": "scored",
  "score": 1.0,
  "passed": true,
  "explanation": "实际输出与参考值一致",
  "evidence": {"actual": "已发货", "expected": "已发货"},
  "duration_ms": 2,
  "error": null
}
```

非 scored 状态的 score/passed 必须为 null。评分器异常不能伪造为 Agent 零分。

### 5.2 评分状态

| 状态 | 含义 | 对必需评分的影响 |
|---|---|---|
| scored | 得到合法评分 | 按 passed 判断 |
| not_applicable | 用例不在已声明适用范围 | 不纳入该评分器分母 |
| missing_evidence | 应有但未采集到输入 | 任务 unknown |
| evaluator_error | 评分服务、实现或解析异常 | 任务 unknown |
| pending_review | Judge 不确定、拒答或冲突需确认 | 任务 unknown |

适用范围在发布时确定，不能因评分困难临时标为不适用。创建校验拒绝必需评分对全部用例都不适用的空判定配置；每个用例必须有至少一项必需任务完成检查。

### 5.3 代表性评分规则

- **Exact Match：** 按路径提取，默认类型敏感深度比较；大小写、空白、数值容差均为显式配置。
- **JSON Schema：** 固定方言和实现版本，禁用不受控的远程引用解析。
- **State Verification：** 对独立采集状态执行白名单声明式断言；Agent 自报状态另标来源，不满足“独立验证”要求。
- **Tool Selection：** 区分允许集合、必要工具及禁止工具；禁止工具为独立硬约束。
- **Tool Arguments：** Schema 合法性与字段值/范围等语义断言分别输出，不互相替代。
- **Trajectory：** 必要步骤覆盖、指定依赖、禁止步骤、相同工具/参数重复阈值。重复检测只是事实性启发，不自动等同无效循环。
- **RAG：** 对去重后的文档 ID 排序计算指标；保留首次出现排名，另报重复率。Recall@K=前 K 相关文档数/全部相关文档数；Hit@K=是否命中；MRR@K=首个相关结果倒数排名。NDCG 使用固定增益 `2^rel-1` 与 `log2(rank+1)` 折扣，IDCG 来自标注相关性。相关性标签缺失是证据不足；标注确认无相关文档时上述排序指标记 not_applicable 并单列此类用例，不能与漏标混淆。

## 6. 执行、判定与指标语义

### 6.1 四层状态

**Run 控制状态：** pending → queued → running → evaluating → completed。执行中可请求取消并进入 cancelling → cancelled；编排无法恢复时进入 failed。completed 表示流程结束，不代表任务或门禁通过。

**CaseExecution 状态：** pending → claimed → invoking → completed / agent_failed / timed_out / infrastructure_failed / outcome_unknown / cancelled。

**Trial 判定：** passed / failed / unknown / excluded。

**Gate：** passed / failed / inconclusive。

GradingBatch 独立维护 pending → running → completed / failed / cancelled。Run 进入终态后不因重评退回 evaluating；新批次自行展示评分进度。重评时历史 TrialAssessment 保留，新判定写入新批次。

Run 同时展示评分完成率和异常计数；正常调用已结束但评分失败时允许 completed 且有 unknown，不将其伪装为全通过。

### 6.2 Trial 判定优先级

1. 任何已确认的安全硬约束违规均保留并导致失败，包括未被选为 canonical 的尝试。
2. 执行超时或已确定的 Agent 错误判 failed；评分可分析已有 Trace，但不使该执行恢复为成功。
3. 执行成功，存在必需评分失败，判 failed，即使另一项评分缺失也不掩盖已证实失败。
4. 没有失败，但必需项缺证据、评分异常或待复核，判 unknown。
5. 全部必需项有效通过，判 passed。
6. 基础设施在未触发 Agent 调用前失败、取消且无已证实失败时为 excluded；是否曾有已知失败另按尝试历史保留。
7. 调用可能发生而结局不确定为 unknown，不能当作纯基础设施排除。

若远端业务操作未知但平台已明确执行超时，执行仍记 timed_out、Trial 记 failed，另存 `remote_outcome=unknown`。若 Worker 崩溃导致无法确定是否完成或超时，执行记 outcome_unknown、Trial 记 unknown。

### 6.3 重复试验与重试

- Run 创建时固定 N 个用例 × R 个 repetition，即计划 Trial 数。
- 每个 Trial 有一个或多个 attempt。attempt_index 不改变试验分母。
- 默认 max_retries=0。授权重试需记录错误类别、退避、重试上限与安全依据。
- 重试仅对明确可恢复且安全的情况生效；Agent 业务错误默认不重试。
- canonical_execution 指向最后一次合法重试得到的结局，不按最高分挑选；无合法重试时指首次尝试。
- 第一尝试成功率、最终成功率、重试率、各次失败均保留；硬约束不会因重试消失。
- 环境重置必须独占隔离环境并有成功校验，不能在共享环境中直接清理其他 Trial 状态。
- 自动重试只发生在 Run 结束前。Run 进入终态后禁止追加执行尝试；人工重执行创建新 Run，关联来源 Run 和选定用例，不改变旧报告。M1 暂不提供重试接口。

### 6.4 聚合口径

设 P/F/U/X 分别为 passed/failed/unknown/excluded，计划数 N=P+F+U+X。

| 指标 | 定义 |
|---|---|
| 任务成功率 | P/(P+F)，无分母时 null |
| 判定覆盖率 | (P+F)/N |
| 已确认成功占比 | P/N |
| Unknown/Excluded | 按原因分别计数，不隐式丢弃 |
| 评分覆盖率 | scored 数 / 预先声明适用的评分项数 |
| 首尝试成功率 | 仅每个 Trial 的首次执行判定，使用相同 P/F/U/X 规则 |
| 重试率 | 有重试的 Trial 数/N |
| 成功调用延迟 | canonical 成功调用的耗时 P50/P95；错误和超时耗时另列 |
| Token/成本 | 所有真实尝试的已知用量相加，Judge 单列，并给出采集覆盖率 |

延迟排除排队和评分时间，另报全流程时长。P95 使用固定最近秩定义，样本少时显示样本数。缺失耗时不补零。加权综合分仅在预声明的参与项齐全时生成，否则为 null，不动态重新归一化掩盖缺失。

## 7. 调度、幂等与故障恢复

### 7.1 创建和投递

同一数据库事务内创建 Run、Trial、初始 CaseExecution、Manifest 和 Outbox。Dispatcher 发布 Outbox 后更新发送状态；发布后崩溃可重发，因此消费者必须幂等。

创建接口支持 Idempotency-Key：项目/调用主体/接口/键构成唯一约束，同键同请求返回原资源，不同请求返回 409。保留期限明确配置，不能早于活跃 Run 生命周期。

### 7.2 租约与写入保护

Worker 通过条件更新领取 pending 执行，写入 owner、lease_expires_at、递增 lease_epoch。心跳只在 owner/epoch 匹配时续约。任何结果和终态写入都附带 epoch；旧 Worker 不能覆盖新所有者结果。

数据库唯一约束保证同一 attempt 只有一个有效终态。同一结果的重复回传不生成重复 EvaluationResult。租约保护本地状态，不能阻止外部系统已经发生的副作用。

M2 调度器需在同一数据库事务内按 Project/Run 固定顺序锁定容量记录、检查可用槽位并领取执行；槽位随租约持有，不能使用各 Worker 的独立计数。Agent 与 Judge 使用不同容量池。停止未确认的远端调用保留占用状态，须经确认或管理员审计处置后回收，避免本地并发上限失真。M1 仅有单进程全局和单 Run 信号量，不实现项目配额。

### 7.3 崩溃恢复决策

| 观测阶段 | 恢复动作 |
|---|---|
| 未进入 invoking | 租约过期后可重新领取，同一 attempt 不重复创建 |
| 已记录 invoking，结果未持久化 | 保守视为可能已调用；查询远端幂等结果或标记 outcome_unknown |
| 远端支持可靠幂等和结果查询 | 使用原操作键恢复，不产生新业务操作 |
| 无法查询但操作只读/具备已验证重置 | 按显式策略创建下一 attempt，记录未知前次及恢复依据 |
| 有副作用且无法确认结果 | 不自动重跑，进入待处理状态，提供带理由的人工恢复操作 |
| 执行结果已持久化，评分未完成 | 仅恢复评分 |
| 结果已持久化，队列未 ACK | 重投命中终态直接返回 |

“写入 invoking 后、真正发送前”同样可能被判为未知，这是为避免误重试而接受的保守边界。平台不声称外部调用恰好一次。

### 7.4 超时与取消

- 分开设置排队期限、连接超时、调用 deadline、评分超时及 Run 总预算。
- 本地计时用单调时钟；审计与 deadline 使用 UTC。
- HTTP 中断等待不等于撤销远端任务；可用时调用独立取消协议并保存确认。
- Python 子进程先协作取消，再在宽限期后终止进程组，记录退出确认。外部系统副作用仍需单独确认。
- 取消请求写库后停止派发；未调用项直接取消，调用中项执行取消协议。
- Run 取消后未解决的调用保留 remote_outcome/停止确认字段，不能显示为“全部已停止”。
- 终态通过数据库条件更新竞争。完成先提交则保留完成；取消/超时先提交则迟到结果作为独立 late_result 制品追加，不能覆盖终态或自动改变门禁。
- 若迟到 Trace 证明安全违规，追加安全发现并使后续报告修订体现该发现；旧报告保留原始证据时间与“已有后续发现”标记。

Celery 的时间限制仅为 Worker 保护，不能替代上述业务状态与取消协议。

## 8. Run Manifest 与复现协议

Manifest 采用版本化 JSON，并对规范内容计算哈希，至少包含：

- BenchmarkVersion ID、内容哈希及用例清单。
- AgentVersion ID、配置快照、代码提交/镜像摘要、Adapter 实现版本。
- Profile 完整内容、评分器版本和制品摘要。
- 模型请求名、可获取的实际模型名、采样参数、种子及 Provider 标识。
- Judge 配置、模型、Prompt/Rubric 内容与版本、解析 Schema。
- repetitions、超时、重试、并发、预算及环境初始化规则。
- 环境基线、隔离方式、重置协议、依赖锁文件/运行镜像标识。
- 密钥引用及可获取的版本标识，禁止明文密钥。
- trace/schema/metric 协议版本。

每次 Execution 另外记录实际请求参数、输入上下文摘要、环境句柄、实际模型响应版本和运行时偏差。无法获得模型固定版本、外部检索快照或环境重置证明时标记 `reproducibility=limited`，列出具体原因。

完整业务内容保存在授权制品中；对 UI 的脱敏视图不应代替审计所需的受控快照。若部署策略要求不可恢复删除敏感原文，应同时标记复现能力下降。

## 9. Trace、制品与观测完整性

采用 OpenTelemetry 上下文关联，但定义独立的 AgentEval 业务 Schema。OTel Span 描述有起止的操作；事件描述操作中的离散事实，不能把 tool_call/tool_result 当作无关联字符串。

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "execution_id": "uuid",
  "trace_id": "32-hex",
  "span_id": "16-hex",
  "parent_span_id": "16-hex-or-null",
  "event_type": "tool_result",
  "tool_call_id": "call-001",
  "component_name": "order-workflow",
  "tool_name": "get_order",
  "sequence": 5,
  "occurred_at": "2026-11-03T08:00:01Z",
  "observed_at": "2026-11-03T08:00:01.020Z",
  "status": "ok",
  "input_ref": "artifact-id",
  "output_ref": "artifact-id",
  "metadata": {}
}
```

- event_id 去重；tool_call_id 关联调用与返回；并发兄弟 Span 不强制全序。
- sequence 在单个事件生产流内递增；跨流顺序依据父子关系与明确依赖，不能仅按接收时间推断。
- 输入输出截断记录原大小、保留策略和是否影响评分；大内容存 Artifact。
- 错误包含来源、类型、是否可重试和脱敏信息；Agent、工具、平台、评分器来源分离。
- Trace 完整性按通道记录，工具完整不代表 LLM 观测完整。
- 对已知禁止行为，部分 Trace 仍可证明违规；对“没有禁止行为”或“覆盖所有步骤”的结论，必须满足完整性要求。
- V1 可使用受控共享卷存 Artifact，API/Worker 一致挂载；数据库保存引用、校验和和访问归属，不暴露文件系统路径。

## 10. LLM Judge 与评分重跑

### 10.1 Judge 请求与输出

MVP 提供一种结构化兼容接口。服务端将 Rubric、参考证据与被测内容分区构造，明确被测输出/Trace 是不可信数据，不允许其改写评分指令。Judge 不提供外部工具执行权限，也不接收平台密钥。

固定模型配置、Prompt 版本、Rubric、采样参数和输出 Schema；即使温度为零也不承诺完全确定。输入超长时按显式策略拒绝或标记截断，不静默丢失决定性证据。

输出至少包含 score、passed、explanation、证据引用和是否需人工复核。校验分数范围、证据引用可解析性以及通过判定与阈值一致性。

### 10.2 故障策略

- 限流、连接故障可有限重试，重试策略及费用计入 Judge 预算。
- 非法 JSON/Schema 失败记 evaluator_error；拒答或无法判断记 pending_review。
- Judge 故障不重跑 Agent，不自动赋零分，不作为环境状态验证的唯一依据。
- 自报置信度仅为原始字段，不当作经校准概率。
- 每次 Judge 请求尝试记录 request_id、版本、输入摘要、脱敏响应、耗时、Token 和错误。

### 10.3 重评与人工复核

对已持久化执行创建新 GradingBatch，不覆盖旧结果。相同配置重评也产生新 revision，用于分析波动；变更配置时必须发布新 Profile 版本。报告和回归固定引用某一批次，不能随“最新结果”自动变化。

每个 GradingBatch 冻结独立 grading_manifest，包含父 Run Manifest 哈希、Profile 评分部分、评分器/Judge 实现版本、实际证据清单及其校验和。变更 Profile 后的重评只允许改变评分配置；新 Profile 的执行要求必须与原始 Run 一致，否则应新建 Run。新增评分所需证据无法从既有执行获得时，返回明确错误或按已声明策略产生 missing_evidence，不补调 Agent。初始 Run Manifest 保留原值，新评分批次通过父引用形成审计链。

人工标签追加到 ReviewRecord。MVP 展示人工与自动结果差异，不自动替换原始分数或门禁。未来人工裁决生效应有版本化决策策略。

校准使用与正式评测分离的小样本集，记录样本构成、人工一致性、自动/人工分歧、重复评分波动和失败比例；不以未验证的统一准确率作为能力承诺。

## 11. 回归比较与质量门禁

### 11.1 比较协议

比较对象固定为两组 Run + GradingBatch，必须处于终态且结果快照已冻结。严格比较检查：

1. 相同 BenchmarkVersion 和内容哈希。
2. 相同 Profile 评分标准、实现摘要、阈值、适用范围和指标协议。
3. 相同 repetitions、重试、超时、会话隔离、并发等关键执行条件。
4. 环境基线兼容；独立环境 ID 可以不同，但初始化状态与重置规则相同。
5. Judge 配置与版本兼容；外部依赖不可锁定时显示限制。
6. Agent/模型/Prompt/工作流的预先声明变更属于比较对象，允许变化；未声明差异必须披露。

严格不匹配时拒绝严格比较，并返回字段级差异；用户可显式创建探索比较。探索比较的 gate 固定为 inconclusive。外部环境兼容性无法确认时也不能默认门禁通过。

### 11.2 用例配对与变化分类

- 同版本用例按 case_id 配对，repetition_index 用于组织样本，不声称随机试验逐次具有因果配对。
- 单次试验：基线 passed、候选 failed 为新增失败；反向为修复。
- 多次试验：基线全通过、候选全失败为新增稳定失败；反向为稳定修复。
- 其他成功率下降/上升标记疑似退化/改善，显示双方 P/F/U/X 和样本数。
- 任一侧存在未知或排除时，显示证据不足，不能仅比较幸存样本后宣称修复。
- Delta Success Rate 使用前述 P/(P+F)，并同时显示覆盖率变化；MVP 不输出统计显著性或因果结论。
- 成功调用延迟变化同时显示样本组成，避免因更多失败导致延迟看似改善。缺失成本显示不可比较。

### 11.3 门禁优先级

1. 已确认安全违规或核心用例失败 → failed。
2. 严格可比性、判定覆盖率、核心用例证据或门禁指标缺失 → inconclusive。
3. 证据充分且任一阈值不满足 → failed。
4. 所有适用条件满足 → passed。

默认覆盖率要求 100%，放宽必须显式配置并写入报告。最低成功率和最大下降使用比例单位，例如 0.02 表示 2 个百分点；延迟以毫秒为单位。核心用例默认所有 repetition 均须通过。

## 12. Bad Case 与归因

分类包括 TASK_FAILURE、TOOL_SELECTION_ERROR、TOOL_ARGUMENT_ERROR、TRAJECTORY_ERROR、RETRIEVAL_ERROR、TIMEOUT、SAFETY_VIOLATION、EVALUATOR_ERROR、INFRASTRUCTURE_ERROR 和 UNKNOWN_OUTCOME。

HALLUCINATION 仅在存在可核验支持证据或明确规则时使用；不能仅凭答案错误自动贴标签。单次执行可多标签。

每条归因保存依据类型 `observed`/`inferred`、证据引用、产生规则/Judge 版本及置信说明。推断标签不自动改变任务判定；硬约束结论必须由 Profile 声明的评分机制给出。

## 13. API 契约

统一前缀 `/api/v1`；列表支持 cursor/limit，默认排序稳定。错误返回 code、message、details、request_id；校验失败定位到字段/行号。无权限返回 401/403，不存在返回 404，冲突/不可比返回 409，非法输入返回 422。

| 领域 | 接口 |
|---|---|
| Project | POST/GET /projects；GET /projects/{id} |
| Agent | POST/GET /agents；GET /agents/{id} |
| AgentVersion | POST/GET /agents/{id}/versions；GET /agent-versions/{id}；POST /agent-versions/{id}/health-check |
| Benchmark | POST/GET /benchmarks；GET /benchmarks/{id} |
| 数据导入 | POST /benchmarks/{id}/imports/validate；POST /benchmarks/{id}/versions |
| 版本与用例 | GET /benchmarks/{id}/versions；GET /benchmark-versions/{id}；GET /benchmark-versions/{id}/cases |
| Profile | POST/GET /profiles；POST/GET /profiles/{id}/versions；GET /profile-versions/{id} |
| Registry | GET /evaluators；GET /adapters |
| Run | POST/GET /evaluations；GET /evaluations/{id}；POST /evaluations/{id}/cancel |
| 执行与评分 | GET /evaluations/{id}/trials；GET /trials/{id}/executions；GET /executions/{id}；GET /evaluations/{id}/results |
| 恢复与重评 | POST /trials/{id}/retry；POST /evaluations/{id}/regrade；GET /evaluations/{id}/grading-batches |
| 证据与分析 | GET /traces/{trace_id}；GET /artifacts/{id}；GET /evaluations/{id}/bad-cases；GET /evaluations/{id}/report |
| 人工标签 | POST /reviews/import；GET /results/{id}/reviews |
| 回归 | POST /regressions/validate；POST /regressions/compare；GET /regressions；GET /regressions/{id} |

创建任务返回 202 及资源位置；创建比较可异步返回 202。取消接口幂等，终态任务返回实际状态。重试接口受安全策略约束，要求 reason；未知副作用结果必须由管理员提供已核验的幂等/重置依据，不能仅点确认绕过规则。

results/report 查询必须指定或返回实际使用的 grading_batch_id，历史链接固定批次。MVP Web 可轮询状态，不依赖额外实时推送设施。所有读写执行鉴权；管理员专属接口独立授权。

## 14. 安全、容量与留存

### 14.1 权限与密钥

- 单租户团队凭据用于业务读写；管理员凭据用于插件、网络策略、密钥及高风险恢复操作。
- API 默认鉴权；健康探针仅输出最小信息。MVP 凭据标识进入审计，非完整个人账号体系。
- 密钥经服务端 SecretProvider 解析，可来自挂载 Secret 或外部密钥服务。若存数据库，采用应用层加密，主密钥在数据库外管理。
- UI/SDK 仅提交 secret_ref，读取接口不回传原值。密钥轮换不修改历史版本，但记录实际引用版本是否可获取。

### 14.2 网络与插件

- HTTP endpoint 默认拒绝，按协议、主机/IP、端口允许清单开放；私网仅显式授权时可达。
- 校验 DNS 解析结果、实际连接地址与每次重定向；默认禁用重定向，防止绕过允许清单。
- Worker 出站策略与应用层校验配合；禁止访问未授权的元数据和管理地址。
- Python Adapter/评分器仅通过部署允许清单注册，无上传执行接口。
- 环境重置只操作绑定环境句柄，禁止任意路径/命令输入。

### 14.3 数据与预算

为上传、输出、Trace 事件数、单事件大小、制品大小、并发、总时长、Judge Token 和费用分别配置上限。达到上限的处理方式固定为拒绝、终止或带完整性标记截断，不能静默丢弃。

成本只能在价格配置和实际用量都已知时估算，记录价格版本；未知价格不等于免费。未预留调用额度时不派发新的 Judge 请求，但已发出请求可能产生费用，记录预算超出事实。

日志脱敏覆盖请求头、参数和异常。脱敏与截断影响评分时产生 missing_evidence，不能继续视为完整证据。

保留策略分配置/元数据、原始内容、Trace 和制品；有效期由部署方显式配置。删除采用审计任务，保留校验和及删除事实；证据删除后的报告显示不可再验证。数据库备份与共享卷制品需要同一恢复窗口，恢复演练纳入部署文档。

## 15. 数据库与索引

结构化身份、状态、版本、时间、租约使用常规列；灵活输入/配置/证据使用 JSONB，超大对象使用 Artifact。禁止将所有字段封装成单一 JSONB 文档。

主要索引：

- 各版本的父对象 ID + version 唯一索引。
- Trial 的 run_id + case_id + repetition_index 唯一索引。
- Execution 的 trial_id + attempt_index 唯一索引。
- Run 的 project_id + created_at + id；Execution 的 status + lease_expires_at。
- Result 的 grading_batch_id + execution_id + evaluator_instance_id 唯一索引。
- Trace 的 execution_id、trace_id、tool_call_id；BadCase 的 run/trial + category。
- Outbox 的 state + next_attempt_at；创建请求的幂等键唯一索引。

迁移使用版本化脚本，升级前备份。已发布数据通过应用层和数据库约束/权限保护不可变，不提供普通 UPDATE 接口。结果追加修订而非覆盖。

## 16. 推荐目录

```text
backend/
  api/             # 鉴权、资源接口、错误契约
  core/            # 配置、SecretProvider、网络与预算策略
  models/          # SQLAlchemy 模型及约束
  schemas/         # Agent/Trace/Profile/Manifest Schema
  adapters/        # base、http、python、langgraph
  harness/         # orchestrator、lease、runner、recovery
  evaluators/      # registry、e2e、trajectory、tool、rag、judge
  tracing/         # 采集、脱敏、完整性
  analysis/        # 聚合、Bad Case
  regression/      # 兼容性、比较、门禁
  artifacts/       # 存储及留存
  workers/         # Celery、Outbox Dispatcher、清理任务
  migrations/
sdk/
frontend/
examples/          # HTTP/Python RAG Agent、LangGraph 工作流
benchmarks/        # 正例、反例、故障与校准集
tests/             # unit、integration、fault、contracts
docs/
docker-compose.yml
README.md
```

## 17. 测试与验收映射

| 类别 | 必测内容 | 对应 PRD |
|---|---|---|
| 契约 | 输入输出、能力、评分状态、未知值与 Schema 版本 | AC-01/02/04 |
| 数据 | 哈希稳定、版本不可变、重复 case_key、错误定位 | AC-03 |
| 判定 | 硬约束优先、评分缺失、超时、基础设施排除、空分母 | AC-02/09 |
| 重试 | repetition 与 attempt 分离、canonical 选择、首尝试指标 | AC-09 |
| 调度 | 重投、重复回传、Outbox 发送后崩溃、旧租约写入 | AC-06/07 |
| 崩溃 | claimed 后、invoking 后、执行后落库前、落库后 ACK 前退出 | AC-06/07 |
| 取消 | 调用中取消、完成竞争、停止未确认、迟到结果 | AC-05 |
| Judge | 超时、限流、拒答、非法结构、证据引用无效、注入内容 | AC-08 |
| Trace | 并发、乱序、重复事件、截断、关联缺失、部分观测 | AC-04/11 |
| 回归 | 配置差异拒绝、探索模式、未知样本、固定批次、门禁优先级 | AC-10 |
| 安全 | 鉴权、密钥泄漏、网络绕过、远程 Schema 引用、插件清单 | AC-12 |
| 部署 | 空库迁移、健康检查、示例任务、备份恢复步骤 | AC-13 |

指标单元测试使用手算可核验的固定数据；集成测试至少覆盖 HTTP 和 Python。LangGraph 使用实际锁定依赖执行示例。Judge 用模拟服务验证故障协议，并在凭据可用时独立执行真实服务校准；模拟通过不能宣称真实模型质量达标。

性能验证使用固定机器、固定模拟 Agent 延迟和明确数据量，检查并发上限、排队、资源占用与持久化行为。报告注明硬件、配置、样本数及外部服务影响，不用未测吞吐量作为验收结果。

## 18. 演示与交付检查

1. 从空数据库启动 Compose 服务并发布示例插件。
2. 注册 HTTP/Python Agent 及 LangGraph 示例版本。
3. 导入包含答案、工具、步骤和检索标签的固定 Benchmark。
4. 发布 Profile，验证缺能力配置被拒绝。
5. 批量执行，展示成功、业务失败、超时和缺失证据。
6. 从 Bad Case 进入执行尝试和对应 Trace/评分证据。
7. 模拟 Worker 退出和重复投递，展示恢复依据与未知结果。
8. 模拟 Judge 故障，独立重评且保留原批次。
9. 发布故意引入退化的 Agent 版本，严格比较并触发门禁。
10. 用不兼容 Profile 创建探索比较，验证不能得到发布通过结论。
11. 导出固定批次报告，检查凭据脱敏和证据引用。

文档、Schema、迁移、测试报告与示例版本一起交付。只有上述链路及 PRD 验收通过，才可将项目状态改为已交付。

## 19. V1.1 相对 V1.0 的主要变化

保留分层架构与 Adapter/Evaluator 插件方向；新增 Trial、GradingBatch、ReviewRecord、Artifact、Outbox 与租约模型，补齐版本与 API；明确评分分母、未知值、取消及迟到结果、外部副作用边界、Run Manifest、回归兼容性和单租户信任边界。实施以里程碑退出条件为准，原六周周期不作为交付承诺。

## 20. 0.5 设计落地与范围修订

新增 environment.py 的注册式 EnvironmentAdapter 协议（prepare、observe、reset），由 Coordinator 调用；Agent 声明的 final_state 不可代替平台独立读取的环境快照。0004 增加 Execution.environment_evidence JSON，保存来源、观察时间、状态哈希及重置结果。证据在重置前采集，重评读取持久化快照。初期实现为每次 Execution 独立 SQLite 退款环境，未知效果保留现场，namespace 已存在时拒绝擦除重建。有状态环境禁用只读声明与自动重试。

trajectory.py 承担规则化轨迹验证，tool_scoring.py 显式接收观测完整性和调用映射策略。检查结果区分 true、false、unknown：已观察到的确定违规优先失败，无法证明缺失则保持证据不足。恢复检查使用工具结束时间与成功工作流 Span 的开始时间；没有时间证据时不能仅凭步骤名称判恢复成功。

EvaluatorMetadata 表达粒度、质量维度、评分机制及能力/证据要求，随评分结果保存并通过注册表 API 暴露。LLM Judge 可配置不同粒度和对应输入/参考路径；非 E2E 使用前校验观测通道，避免为缺失过程数据发起模型请求。

metrics.py 在 canonical Execution 上按评分项聚合平均得分、通过率、覆盖率及样本量，保留历史尝试的硬约束违规。状态、工具、轨迹、可靠性、Token 与 Judge 指标分别展示；不适用的度量为 null。P95 和工具错误率门禁有最少样本与证据覆盖条件。准确费用仍依赖完整 usage、可信价格和计费版本，不以 Token 或调用次数替代真实金额。

calibration.py 对固定批次和导入标签计算一致率、混淆矩阵、评分覆盖、错误/复核率及重复波动，标签包含来源、理由和哈希，不覆盖 EvaluationResult。它是校准分析能力，尚未落地 ReviewRecord 的完整人工审批与审计工作流。开发者种子标签、协议模拟及真实模型校准分别标记，不混作独立人工验收。

retrieval.py 提供不同算法的 SQLite FTS5 Retriever 与 TF-IDF Reranker，另提供部署注册的 HTTP 组件入口。examples/rag_service.py 独立进程接收查询或候选并返回有序文档、分数和方法元数据，核心 Runner 无需因接入而修改。输入与返回仍遵守可信部署及精确网络允许清单；此实现不等同于生产组件鉴权与网络隔离已验收。

完整 Schema、示例、运行方式、边界与新增验收见 [0.5 验收说明](V0.5_ACCEPTANCE.md)。原设计中的 Artifact、ReviewRecord、全面脱敏、安全隔离及全套部署演练仍为待交付部分。

## 21. 0.6 产品闭环实现

新增 product.py 与 product_contracts.py，仍在同一 FastAPI 服务和鉴权边界下安装路由。向导复用 validate_agent_config、Profile/Case 预检与原 create_run；同一 Session 事务创建项目/Agent/数据集/Profile/Run/OnboardingReceipt，固定请求键防重复，不另建执行器。接入检查对可信插件只校验注册，HTTP 只发送允许清单内的 HEAD 探测，明确区分声明能力与实测观测。

0006 固定 DDL 增加 QualityIssue、IssueEvent、RegressionCollection、ReleaseDecision、OnboardingReceipt。QualityIssue 唯一约束为批次+执行；来源证据绑定原评分及用例内容哈希。人工更新受 revision 与状态机约束，resolved 仅能由同项目、相同输入/断言、兼容评分/执行/环境/Judge/引擎且全部重复 Trial 通过的新批次产生。历史硬约束失败保持单独事实及来源执行 ID。

RegressionCollection 为项目指向普通 Benchmark；沉淀案例以不可变版本追加，内容重复复用已有版本，key 冲突使用内容哈希后缀，旧测试集不修改。ReleaseDecision 在 Project 锁内同时读取固定比较和当前同案例范围的开放阻断项，冻结问题修订号、版本、门禁和覆盖信息，提供三态建议及鉴权导出。它不修改原 Comparison、GradingBatch 或生产系统。

工作台在服务端按项目计算总数、全局阻断优先队列和候选批次指标，避免从 UI 当前页推断全局状态。版本比较选择器使用独立分页查询读取项目全部已结束任务；旧资源管理仍保留。新增前端产品页面复用现有 Trace、证据组件和 API 令牌内存策略。

产品工作流、数据边界、API、升级与验收见 [0.6 产品闭环](V0.6_PRODUCT_ACCEPTANCE.md)。单可信团队边界不变，人工负责人字段不等同于认证身份。真实用户指标和真实业务接入不由集成样例替代。


## 22. 0.7 IssueScope 与统一评测范围（覆盖 0.6 对应实现）

0007 增加 IssueScope（issue_id 唯一、agent_id、benchmark_id、case_hash、version_ids JSON），QualityPolicy（project_id 唯一、ingestion_mode），QualityIssue.kind 和 merged_into_id。IssueScope 将稳定 Agent/Benchmark 身份与输入/断言摘要关联，版本使用不可变 ID 集合，默认来源版本。迁移固定 DDL 并反射旧表回填，不导入未来 ORM；确认事件用于保留旧重新开放 Issue 类型。

issue_scopes.py 提供异常采集和适用范围匹配，scope_api.py 提供统一资源归属校验、范围查询、批量治理与范围修改。Coordinator.grade 在同一 Project 锁和事务内写入 TrialAssessment、批次完成及自动异常；重复处理沿用批次/执行唯一约束。模式切换与批量处理使用相同 Project 锁；每条 revision 在变更前全部验证，合并保留来源证据、事件及目标指针。

workbench 将候选固定批次、比较和决策查询限制在所选 AgentVersion 与 BenchmarkVersion；基线明确匹配，缺失基线时不回退到其他版本比较。app 中 EvaluationScope Context 在工作台、问题列表和发布列表间共享范围；切换期间清空旧数据，异步迟到结果不得覆盖新范围。项目总览使用独立 API。

发布在 Project 锁内筛选相同 Agent、Benchmark、用例内容和适用版本的正式开放阻断项；发布快照使用 JSON 可序列化的范围及问题修订信息。跨 Agent 或不同 Benchmark 固定版本仅能形成 evidence insufficient 发布建议。复测校验同 Agent、同 Benchmark 身份及原有策略和用例兼容性。历史快照和历史评分不重写；新发布使用 release-2.0 策略。

接口、状态机、迁移和回归边界见 [0.7 范围与异常治理](V0.7_SCOPE_ACCEPTANCE.md)。
