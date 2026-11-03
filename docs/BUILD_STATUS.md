# AgentEval 构建状态

**构建编号：** 0.2.0 / M1 + Web 工作台

**日期：** 2026-11-03  
**状态：** 本地评测、回归闭环与真实 API 驱动的前端已实现；完整 MVP 尚未验收。

PRD 与详细设计描述最终目标，本文件区分当前已实现与后续能力，不应把目标文档中的全部功能理解为已经可用。

## 1. 本轮交付

- FastAPI 服务、Bearer token 鉴权、受保护 OpenAPI、2 MiB 请求上限。
- SQLAlchemy 模型与固定 DDL 的 Alembic 初始迁移，默认 SQLite，支持 PostgreSQL 连接配置。
- Project、Agent/AgentVersion、Benchmark/BenchmarkVersion/TestCase、ProfileVersion。
- Trial、Execution、GradingBatch、EvaluationResult、TrialAssessment 分离；重评不覆盖历史判定。
- 可信 Python 子进程接入、HTTP JSON 接入、调用超时与输出大小限制。
- Exact Match、JSON Schema、工具选择、参数值约束、步骤依赖与四项 RAG 排序指标。
- 批量执行、重复试验、单 Run/单进程并发限制、基本取消、幂等创建。
- 输出、观测通道、评分证据、失败项查询及固定评分批次报告。
- 严格/探索比较、成功率变化、新增失败、修复项及基础门禁。
- 源码 SDK、两条用例、两个 Python 版本、HTTP 示例、自动端到端脚本。
- Docker/Compose 配置、依赖锁定和 GitHub Actions 校验工作流。
- React/TypeScript/Vite 工作台：登录、项目切换、资源与版本管理、任务创建、执行证据、历史批次和回归报告。
- JSON/JSONL 文件解析与用例预览、移动端导航、空数据引导和一键创建真实演示项目。
- FastAPI 同源提供前端构建产物，Vite 开发代理和 Windows 本地启动脚本。

## 2. 关键实施决策

1. Trial 不保存跨批次共享 verdict；TrialAssessment 按 Trial + GradingBatch 唯一保存判定。
2. 已终结 Run 不追加执行尝试。M1 禁用自动重试；重执行创建新 Run。
3. 重评使用独立 GradingBatch，不改变已完成 Run 状态；本轮仅支持原评分配置重评。
4. 所有路径配置使用 RFC 6901 JSON Pointer，与原说明文档中的点号示例不同；以实际 OpenAPI 和源码契约为准。
5. 相同硬约束判定不被加权抵消；缺失证据和评分器异常不自动计零分。
6. RAG 目前要求至少一个已标注相关文档；零相关文档的 not_applicable 扩展尚未实现。
7. 比较对有波动/未知证据采取保守门禁，不输出统计显著性结论。

## 3. 当前默认上限与契约

| 项目 | 默认/范围 |
|---|---|
| API token | 启动要求至少 16 字符 |
| 请求体 | 2 MiB，包含分块传输 |
| Agent 输出 | 1 MiB，超限记 Agent 协议失败 |
| Benchmark | 每版本 1–1000 条用例 |
| Repetitions | 1–10，默认 1 |
| Agent 超时 | 大于 0 且不超过 300 秒，默认 30 秒 |
| 单 Run 并发 | 1–16，默认 4 |
| 单 API 进程总并发 | 8 |
| 自动重试 | 固定 0 |
| 每页数量 | 默认 50，上限 100；部分版本子列表尚未分页 |
| HTTP | 精确主机:端口允许清单；禁用重定向与环境代理 |
| 评分器版本 | 当前固定 1.0 |
| 指标缺失值 | null，不填零 |

## 4. 验证记录

本地 Windows / Python 3.12 验证包括：

- 自动化测试：34 项通过，覆盖规则、判定、API、项目过滤、实际 HTTP/Python 调用、超时和迁移；依赖库有一条 TestClient 弃用提示，未影响运行。
- 前端 TypeScript 检查与 Vite 生产构建通过；3 项 Playwright 真实浏览器测试通过，覆盖演示、手动配置与移动端。测试截图位于本机 `.build/console-*.png`，未提交业务数据库或令牌。
- 真实进程 smoke：从空数据库启动 API 与 HTTP 示例，通过 SDK 完成评测和比较。
- 示例基线成功率 1.0、候选成功率 0.5，新增失败 order-1001，门禁 failed；HTTP 示例成功率 1.0。
- `docker compose config --quiet` 解析通过。

以上数值仅为固定模拟数据的功能检查。Docker 引擎未启动，因此本机没有完成镜像构建/容器运行验证。上一轮 GitHub CI 的测试、真实 API smoke 和 PostgreSQL 重复迁移已通过；本轮新增浏览器 CI，状态以提交对应 Actions 为准。

## 5. 尚未实现

| 后续阶段 | 能力 |
|---|---|
| M2 可靠执行 | Celery/Redis、Outbox、租约与 fencing、项目级容量、故障恢复、受限重试 |
| M3 评测扩展 | LangGraph、LLM Judge、OTel Span/事件、独立状态探针、完整参数 Schema 评分 |
| M4 产品交付 | 人工标签导入、报告导出、分页与大数据量交互完善；基础 Web 和导入预览已提前交付 |
| 基础设施完善 | 密钥提供器、脱敏/制品/留存、预算计量、DNS/IP 与出站网络策略 |

当前不能宣称 AC-05/06/07/08/12/13 已全部满足；例如 API 创建幂等不等于队列重复投递幂等，迁移成功也不等于 Compose 运行验收通过。

## 6. 下一轮顺序

根据用户反馈，已将基础 Web 提前交付。后续实现 Outbox、Worker、执行租约、容量限制与中断恢复，并补相应故障测试；保留现有 API、TrialAssessment 与批次契约，再接入 Judge/LangGraph。每轮通过检查后提交并同步 GitHub，失败或未验证项在此文件中继续记录。
