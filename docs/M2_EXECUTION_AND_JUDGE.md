# M2 执行、LangGraph 与 Judge 说明

## 数据与投递

创建 Run 时，同一事务保存试验、执行尝试、评分批次及每个执行的 WorkItem。Dispatcher 只向 Broker 发布工作项 ID，Redis 不承担唯一状态存储。

投递后更新 next_delivery。发送后未标记或消息丢失时可再次投递；Worker 根据数据库状态领取，重复消息不会产生第二次有效调用。数据库与外部系统之间仍不保证端到端恰好一次执行。

工作项状态：pending → published → running → done；可能产生未知副作用时为 blocked。Broker 异常只记录脱敏错误码并重试投递，不直接判 Agent 失败。

## 租约、容量与恢复

领取时按 Project 锁检查项目及 Run 容量。SQLite 使用立即事务，PostgreSQL 使用项目行锁。执行和评分容量分开。领取递增 epoch，写 owner/lease_until；心跳续租。结果写入必须匹配 owner、epoch、有效租约。

- 评分工作过期可恢复，已持久化评分项复用，不重调 Agent。
- 可能已调用 Agent 的工作过期，转 outcome_unknown；旧结果只能追加到 late_result。
- 未知副作用保留容量，等待管理员核验；明确只读且 Profile 允许时可新建 attempt。
- API 重启不会撤销仍有效的 Worker 租约。

重试不增加 Trial 分母；canonical 选择最后一次合法尝试，不挑最高分。所有尝试已证实的硬约束失败均保留。Run 终态后重执行需创建新 Run。

外部调用可能在 Worker 退出后继续运行。子进程生命周期控制不是通用业务沙箱。跨主机部署需要时钟同步，当前租约不宣称能容忍任意时钟漂移。

## 未知占用处置

用 `GET /api/v1/evaluations/{id}/work-items` 查看 blocked，用 `audit` 查看恢复事件。管理员核验远端操作结束或允许释放容量后：

```text
POST /api/v1/executions/{execution_id}/release-capacity
Authorization: Bearer <team-token>
X-Admin-Token: <AGENTEVAL_ADMIN_TOKEN>

{"reason": "已在外部系统核验操作结束，本次仅释放容量"}
```

该动作记录审计，使旧 epoch 失效，不重跑、不修改原判定。未配置 Admin token 时拒绝该操作。迟到证据保留，但尚不自动修订旧报告。

## LangGraph 接入

GRAPH_PLUGINS 是部署允许清单，工厂返回编译后的 StateGraph。适配器在子进程内消费 `stream_mode=[updates, values]`、`version=v2`，实际节点更新生成 workflow_steps，最终状态映射为输出与观测。

示例 `example.order.graph` 执行 lookup → respond。工具与检索通道需显式埋点，不能根据最终答案猜测。复杂并行/循环图需要相应业务约束，当前不提供通用图等价性判断。

接口依据：[LangGraph 流式事件文档](https://docs.langchain.com/oss/python/langgraph/streaming)。具体依赖版本见 requirements.lock。

## Judge 协议与预算

端点和模型由部署环境配置；Profile 保存 Rubric 与超时。请求采用 chat-completion 兼容协议，包含 model、messages、temperature、max_tokens 和 JSON Schema response_format。服务商必须支持这一结构化输出方式，并非任意文本生成端点均兼容。

输出包含 score（0–1）、explanation、evidence 来源名称和 needs_review。服务端根据阈值计算通过与否。被测内容是不可信数据，Judge 无工具权限；提示分区不能保证消除裁判偏差或注入风险。

- 非法 JSON/结构/分数：evaluator_error。
- 拒答或 needs_review：pending_review。
- 证据缺失/超长：missing_evidence，不静默截断。
- 传输故障及 429/502/503/504：最多再请求一次。
- 请求前持久化预算占用，中断也计数；重评和恢复共享 Run 调用上限。

JudgeCall 保存输入哈希、请求模型名、Prompt 版本、响应哈希、用量和结果，不保存密钥。模型别名可变时复现仍受限。调用数预算不是精确金额预算；未验证真实裁判准确率。重评产生新批次，不覆盖历史判定。

## 部署与验证

Compose 包含 PostgreSQL、Redis、API、Worker、Dispatcher。API 迁移并健康后再启动执行进程，Redis 使用 AOF。可通过 `docker compose up --scale worker=2 --build` 扩展；数据库容量上限仍生效。

Celery 使用 JSON、late ack、prefetch=1。Broker visibility timeout 不替代数据库租约。参考：[Celery Redis 投递说明](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)。

升级前停止旧服务并备份数据库。0002 为未完成的旧执行补建工作项；旧 invoking 转未知且 blocked，绝不盲目重调。

`scripts/queue_smoke.py` 验证 API 重启、独立 Worker、实际图、裁判协议和强制退出。默认 fakeredis/Judge 均为显式协议模拟；CI 配置真实 Redis/PostgreSQL 后执行同一脚本。测试使用独立数据，不调用付费模型。
