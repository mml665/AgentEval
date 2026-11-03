# AgentEval 构建状态

**版本：** 0.4.0 / 健康、预算、分页、导出、Trace、工具与组件

**日期：** 2026-11-03

当前已实现前后端闭环与可靠执行协议；完整 MVP 的安全、运维及真实裁判校准尚未全部验收。

## 0.4 本轮交付

- 后台健康/就绪检查、异常退避恢复、API/Worker/Dispatcher 心跳与积压页面。
- Judge Run 预算冻结，包含重评/中断请求；旧运行首次使用的冻结来源可追溯。
- 工作台服务端分页、批量关联查询、报告批量聚合、按执行读取评分证据。
- 固定评分批次 JSON/CSV/HTML 导出，内容哈希、CSV 注入处理、HTML 转义与基础字段脱敏。
- OTel SDK 父子 Span、事件、步骤与工具耗时、状态更新；增量 IPC 保留失败/超时前轨迹；树和时间线查看。
- tool_call 逐调用 ID 评分，支持参数/结果 Schema、值断言、顺序和输出使用规则；支持同工具多次调用。
- 独立 Retriever/Reranker 注册表和组件评测接口、Schema 预检及专用页面。

完整范围与边界见 [0.4 验收说明](V0.4_ACCEPTANCE.md)。

## 已实现

- React 工作台、项目/资源/版本管理、JSON/JSONL 预览、评分证据、历史批次和回归报告。
- 不可变版本接口、配置哈希、Run Manifest、Trial/Execution/GradingBatch/TrialAssessment 分离。
- 事务内 WorkItem、独立 Celery Worker/Dispatcher、Broker 断连后再次投递。
- Project/Run 容量、数据库租约/心跳、执行代次、重复投递和旧结果保护。
- API 重启保留有效租约；Worker 过期转未知；未知副作用保留容量，管理员带理由释放。
- 默认关闭重试；显式 read_only Agent 可针对指定失败重试，最多两次，独立 attempt 记录。
- HTTP、可信 Python、实际 LangGraph 图接入；仅允许部署注册的插件。
- 确定性规则、轨迹/工具/RAG 评分，以及结构化 LLM Judge。
- Judge 环境密钥、冻结端点/模型、Prompt 版本、结构校验、限次重试、审计及持久化调用预算。
- 固定评分批次的严格/探索回归和基础门禁。
- API/PostgreSQL/Redis/Worker/Dispatcher Compose 配置、前端多阶段镜像与增量迁移。

## 验证记录

- 62 项 Python 测试通过：包括旧版本迁移、重复投递、跨 Coordinator 容量、API 重启保护、租约过期、迟到结果拒绝、只读重试及 Judge 故障。
- 前端生产构建与 4 项真实浏览器测试通过；手动配置流程使用实际 LangGraph Agent。
- HTTP/API smoke 保持基线 100%、候选 50%、新增失败 order-1001 的预期结果。
- 独立进程组合 smoke 通过：API 重启保留排队任务；Worker 完成图执行与裁判协议调用；强制结束 Worker 后结果未知，未静默重执行。
- 本地组合 smoke 使用 fakeredis TCP 与裁判协议模拟；CI 额外使用真实 Redis + PostgreSQL，状态以提交对应 Actions 为准。
- Compose 配置解析通过；本机 Docker 引擎未启动，未完成本机容器启动验收。

以上成功率仅用于固定数据的功能检查。尚未使用真实付费裁判，不宣称真实模型质量已校准。

## 默认配置

| 项目 | 默认与边界 |
|---|---|
| 模式 | local；Compose 使用 celery |
| 项目执行上限 | 8，跨 Worker 共享，不是全局机器资源上限 |
| Run 执行上限 | 4，可配置 1–16 |
| 项目评分批次并发 | 1，与 Agent 容量分开 |
| 租约 / 心跳 | 30 秒 / 租约的三分之一 |
| 重试 | 默认 0、最多 2，仅显式 read_only |
| Judge | 每评分最多 2 次；Run 默认 100 次，含中断和重评 |
| 超时 | Agent 默认 30 秒、最大 300；Judge 默认 15 秒、最大 60 |
| 请求/输出 | API 2 MiB、Agent 输出 1 MiB、Judge 响应 64 KiB |
| 管理员处置 | 团队 token + 独立 Admin token + 审计理由 |

## 仍需完善

- 真实 Judge 校准、业务标注集、一致性与波动分析。
- 有状态环境重置、远端幂等查询和取消确认；当前只允许只读安全重试。
- 独立环境状态探针；外部系统补充埋点与复杂业务语义评分。OTel 树和工具参数 Schema 已交付。
- 密钥版本提供器、全面脱敏/制品/留存、DNS/IP 与出站网络策略。
- 精确 Token/费用计量、人工标签及超大结果集的流式导出；当前已有分页与基础报告导出。
- 长期压力和故障测试，以及完整 Compose 部署演练。

管理员释放仅解决容量占用，不把 unknown 改为 passed。迟到输出单独保存，尚不自动触发新的安全门禁报告。

下一轮优先真实 Judge 校准与 Compose 演练，再完善状态验证、脱敏、Trace 和导出。每轮通过检查后同步 GitHub。
