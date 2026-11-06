# AgentEval

面向 Agent 开发团队的自动化质量评测与回归决策平台。

**当前版本：0.12.1 · 最后更新：2026-11-06。** 帮助团队回答：新版本是否更好、哪里退化、证据是什么、是否具备发布条件。主线为“接入 → 评测 → 诊断 → 修复 → 回归 → 发布建议”；沿用异构 Adapter、独立状态验证、Trace 和持久化执行核心。

| 能力 | 当前状态与边界 |
|---|---|
| 质量工作台 | Agent / 候选 / 基线 / Benchmark 选择器统一质量指标、问题和发布范围；全项目统计独立到项目总览 |
| 任务查询与摘要 | 服务端跨页搜索/筛选/排序；执行状态与固定批次质量结论分列，失败可直达来源批次；资源刷新中禁用翻页 |
| 评测创建与复用 | 新接入/已有版本双入口、历史配置复制、统一预检摘要和同键提交恢复；复用固定资源，不重复注册 |
| 用例与基线 | JSON/JSONL 表格/单条编辑、按行字段预检及版本差异；显式保存基线与固定批次，分享 URL 可恢复 |
| 个人草稿 | 服务端按成员/项目隔离，未保存离开提示；提交响应丢失后恢复草稿并查询原结果 |
| Bad Case 闭环 | 默认自动收集待确认异常，确认后进入 Issue；支持手动模式、批量忽略/合并/分配、规则事实与预期/实际差异及兼容复测 |
| 版本发布决策 | 当前评测范围满足门禁 / 阻断 / 证据不足，按 Agent、Benchmark、用例内容及显式适用版本匹配开放阻断项，冻结快照，另列当前有效性与重评提示，支持 JSON/HTML 报告 |
| Agent 接入 | HTTP、可信 Python、实际 LangGraph；真实业务双项目验收待提供资源，本地集成样例不替代该验收 |
| 团队身份 | 个人会话、部署管理员/项目评测/只读角色，服务端项目隔离，成员分配与实际操作者审计；旧共享 Token 显式兼容 |
| HTTP 鉴权 | 服务端版本化环境秘密引用、Bearer/Header API Key、HEAD 与显式测试分开、凭据失效及协议错误反馈 |
| 展示与原文保护 | 业务 API/导出共用版本化字段路径及内容规则；原文单独个人授权、填写理由并审计，历史证据及评分不改写 |
| 留存与归档 | 可配置期限提醒、发布引用及 response/Trace 字符量观测；签名项目快照与隔离空库恢复，不自动删除原库 |
| 四粒度评测 | 端到端、轨迹、逐工具调用、独立组件；无观测或不适用的指标保留未知/空值 |
| 独立环境验证 | 平台读取隔离 SQLite 状态，快照先提交再重置；Agent 自述不能替代独立证据 |
| 证据与恢复 | 平台来源标记、状态哈希校验、租约保护、崩溃前快照保留；未确认重置不放行门禁 |
| 工具故障注入 | 受控 Python/LangGraph 工具支持 Mock、异常和超时；并发/递归调用序号按入口分配 |
| 回归与统计 | Trial/Attempt 覆盖率分别报告；严格比较校验环境配置，支持样本量受限的 P95/工具错误率门禁 |
| Judge | 已有结构化调用、预算、标签校准流程；真实模型一致性和稳定性报告待实际模型与复核标签 |
| 部署 | 0.12 在独立 Compose 栈验证五服务启动、队列/Worker 故障、全库及环境卷恢复、同结构版本回滚；生产容量与恢复目标待实际验收 |

**本轮交付**覆盖产品复查 R12-01～05，包括用例原文保护、历史运行和批次锁定、同键提交恢复、统一创建评测入口，以及范围和资源选项按需分页。实测与接口见 [0.12.1 产品复查交付](docs/V0.12.1_REVIEW_DELIVERY.md)。

- [团队优化 PRD](docs/TEAM_RELEASE_OPTIMIZATION_SPEC.md)
- [0.8 批次 A 交付与后续任务](docs/V0.8_TEAM_BATCH_A.md)
- [0.9 团队身份、HTTP 凭据、迁移与 SDK/CI 接入](docs/V0.9_TEAM_FOUNDATION.md)
- [0.10 脱敏、原文授权、留存与签名归档恢复](docs/V0.10_EVIDENCE_PROTECTION.md)
- [0.11 产品流程、显式基线与个人草稿](docs/V0.11_PRODUCT_COMPLETION.md)
- [0.12 产品复查与修改清单](docs/V0.12_PRODUCT_REVIEW_2026-11-05.md)
- [0.12 当前发布包部署与恢复验收](docs/V0.12_OPERATIONS.md)
- [团队身份/会话与数据保护设计](docs/TEAM_IDENTITY_DESIGN.md)
- [项目 PRD](docs/AgentEval_PRD_V1.1.md)
- [系统设计](docs/AgentEval_系统详细设计_V1.1.md)
- [实际构建状态、限制与验证记录](docs/BUILD_STATUS.md)
- [0.7 评测范围与异常治理](docs/V0.7_SCOPE_ACCEPTANCE.md)
- [0.6 产品闭环与验收](docs/V0.6_PRODUCT_ACCEPTANCE.md)
- [0.5.2 证据与恢复边界](docs/V0.5.2_HARDENING.md)
- [0.5.1 核心修复与待验收项](docs/V0.5.1_CORE_ACCEPTANCE.md)
- [0.5 使用契约与验收边界](docs/V0.5_ACCEPTANCE.md)

## 第一次使用

安装并启动后，打开工作台的“开始评测”：

1. 连接 HTTP Agent 或选择部署好的 Python/LangGraph 插件。HTTP 使用 HEAD 检查响应；声明能力将在实际执行中核验。
2. 选择回答、工具、轨迹、RAG 或独立状态目标；平台生成兼容配置。
3. 导入带预期结果的 JSON/JSONL 并执行，直接查看评分事实与 Trace。

默认在评分完成的同一事务自动收集“待确认异常”，不会直接认定为开发缺陷；管理员也可在“团队与凭据”切换为仅手动导入。Bad Case 手动入口继续保留，首屏优先显示待确认/待修复/待复测、成员负责人和“分配给我”。异常确认后进入修复与复测流程，支持批量确认、忽略、合并和分配。复测必须保留相同输入/断言及兼容策略，全部对应 Trial 通过后才能关闭；可将案例沉淀到长期回归集。首页先选择 Agent、候选版本、基线版本和 Benchmark 固定版本；Bad Case 与发布列表沿用此范围。Issue 默认仅适用于来源版本，可在详情显式选择受影响版本（版本 ID 集合，不推断后续版本）；历史问题不会无条件阻断新版本。版本比较页面可直接“生成发布评估”，或从“发布决策”选择历史比较；证据不足会保留第三态，不默认可发布。

预览保留历史“AgentEval 0.8.0 产品闭环演示”等项目，包含阻断、修复与可发布、探索比较证据不足的记录。它使用本地退款集成样例，不代表真实业务 Agent 已验收。首次有效评测时间 ≤15 分钟等建议指标仍需真实用户试用测量。

## 本地启动

要求 Python 3.11+；本地验证环境为 Windows / Python 3.12。所有命令在仓库根目录执行。默认使用 SQLite，无需 Docker 或模型密钥。

PowerShell：

```powershell
uv --cache-dir .uv-cache venv .venv
uv --cache-dir .uv-cache pip install --python .venv/Scripts/python.exe -r requirements.lock
$env:PYTHONPATH = 'backend'
$env:AGENTEVAL_API_TOKEN = 'replace-with-your-random-token-at-least-16-characters'
.venv/Scripts/python.exe -m agenteval init-db
.venv/Scripts/python.exe -m agenteval serve
```

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
export PYTHONPATH=backend
export AGENTEVAL_API_TOKEN='replace-with-your-random-token-at-least-16-characters'
.venv/bin/python -m agenteval init-db
.venv/bin/python -m agenteval serve
```

API 默认监听 `127.0.0.1:8000`。`/health` 和 `/ready` 为公开健康探针；业务接口接受合法会话或 Bearer 机器/兼容 Token。受保护的 OpenAPI 文档位于 `/api/v1/openapi.json`，未提供 Swagger 页面。`.env.example` 是配置参考，本地命令不自动加载 `.env`，应使用环境变量。以上命令为 legacy 兼容启动；个人账户需切换 `AGENTEVAL_AUTH_MODE=team`、关闭旧 Token、配置准确 Origin/Cookie 并由 CLI `init-admin` 一次性初始化，完整命令见 [0.9 升级说明](docs/V0.9_TEAM_FOUNDATION.md#2-开启个人账户模式)。

本地模式默认以**单 API 进程**运行；独立队列模式通过多个 Worker 扩展。首次启动前执行迁移；应用启动不会隐式建表。数据库文件位于 `data/agenteval.db`，不会进入 Git。

## 前端页面

首次构建前端需要 Node.js 20.19+ 或 22.12+（本地验证为 Node 24）：

```bash
cd frontend
npm ci
npm run build
cd ..
```

构建后重启 API，打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。前端由同一个 FastAPI 服务提供，无需另起静态服务器；没有构建产物时，API 仍可独立工作，但根路径没有页面。

Windows 已安装依赖并完成前端构建后，也可直接在仓库根目录执行：

```powershell
.\scripts\start-local.ps1
```

脚本初始化数据库并启动服务；未设置环境变量时，仅从本机 `.env` 读取访问令牌；不存在有效令牌时生成随机令牌并保存。它不加载 `.env` 中其他配置。登录时使用 `AGENTEVAL_API_TOKEN` 的值，令牌不会保存到浏览器存储。

页面包括：质量工作台、三步向导、Bad Case 管理、发布决策，以及 Agent/Benchmark/Profile 资源、任务、评分证据和回归比较。空项目显示配置引导，可点击“创建演示项目”生成真实持久化示例并执行两次评测。

前端开发时保持 API 在 8000 端口运行，在 `frontend` 目录执行 `npm run dev`，访问终端显示的 Vite 地址。开发服务器通过同源代理访问 API；不同 API 地址可通过 `AGENTEVAL_DEV_API` 配置。

浏览器验收在前端构建后执行：

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

测试自动启动使用独立临时数据库的 API，不影响正常数据。Windows 默认使用本机 Chrome；其他系统使用 Playwright Chromium。覆盖错误令牌、演示全流程、手动资源配置、重评与历史批次、回归证据和移动端布局。

## 执行版本回归示例

保持 API 运行，在另一个终端配置相同的 `AGENTEVAL_API_TOKEN` 后执行：

```powershell
.venv/Scripts/python.exe -m examples.demo
```

示例会创建项目、两个 Agent 版本、两条用例及六个评分项。它验证以下真实结果：

```text
基线成功率：100%
候选成功率：50%
新增失败：order-1001
门禁：failed
```

示例数据仅用于验证框架行为，不代表真实 Agent 能力或性能基准。每次运行创建独立项目，不删除历史数据。

验证 HTTP 接入时，在第三个终端运行示例 Agent：

```powershell
$env:PYTHONPATH = 'backend'
.venv/Scripts/python.exe -m uvicorn examples.http_agent:app --host 127.0.0.1 --port 9001
```

然后执行 `python -m examples.demo --with-http`（使用虚拟环境 Python）。默认仅允许 HTTP Agent 访问 `127.0.0.1:9001` / `localhost:9001`；注册其他服务前设置精确的 `AGENTEVAL_HTTP_ALLOWLIST=hostname:port`。重定向、带凭据和查询参数的 endpoint 均被拒绝，HTTP 客户端不继承系统代理。

## 自动化验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/smoke.py
.venv/Scripts/python.exe scripts/v05_smoke.py
.venv/Scripts/python.exe scripts/queue_smoke.py
.venv/Scripts/python.exe scripts/product_smoke.py
```

测试包括真实 HTTP/Python/LangGraph 调用、超时、重复投递、租约过期、旧结果拒绝、未知结果保留、Judge 故障、历史重评及旧版本迁移。`scripts/smoke.py` 自动启动临时 API 和 HTTP Agent，通过 SDK 执行完整回归，最后关闭进程；临时数据库和证据位于被 Git 忽略的 `.build/`。

测试需要本机回环网络与子进程权限。若沙箱阻止这些能力，应在受信任的本地终端执行，不能以单元测试通过替代真实链路验证。

## 主要接口

| 功能 | 接口 |
|---|---|
| 项目 | POST/GET `/api/v1/projects` |
| Agent | POST/GET `/api/v1/agents` |
| Agent 版本 | POST/GET `/api/v1/agents/{id}/versions` |
| 数据集 | POST/GET `/api/v1/benchmarks` |
| 发布数据集版本 | POST `/api/v1/benchmarks/{id}/versions` |
| 评分方案 | POST/GET `/api/v1/profile-versions` |
| 评分器 / 环境注册表 | GET `/api/v1/evaluators`、`/api/v1/environments` |
| Judge 标签校准 | POST `/api/v1/evaluations/{id}/calibration` |
| 执行 | POST/GET `/api/v1/evaluations` |
| 状态与批次 | GET `/api/v1/evaluations/{id}` |
| 报告/评分/失败项 | GET `/api/v1/evaluations/{id}/report`、`results`、`bad-cases` |
| 重评 | POST `/api/v1/evaluations/{id}/regrade` |
| 执行与观测 | GET `/api/v1/executions/{id}`、`/api/v1/traces/{trace_id}` |
| 回归 | POST `/api/v1/regressions/compare` |
| 投递/审计/裁判调用 | GET `/api/v1/evaluations/{id}/work-items`、`audit`、`judge-calls` |
| 管理员释放占用 | POST `/api/v1/executions/{id}/release-capacity`（另需 `X-Admin-Token` 与理由） |
| 工作台批量分页 | GET `/api/v1/workspace/{project_id}`，支持各资源 offset/limit |
| 就绪 / 运行健康 | GET `/ready`、`/api/v1/operations/health` |
| 结构化 Trace | GET `/api/v1/executions/{id}/trace` |
| Judge 预算 | GET `/api/v1/evaluations/{id}/judge-budget` |
| 固定批次导出 | GET `/api/v1/evaluations/{id}/export?grading_batch_id=…&format=json`（另支持 csv/html） |
| 独立组件 | GET `/api/v1/components`；POST `/api/v1/component-evaluations` |
| 质量工作台 | GET `/api/v1/quality/workbench/{project_id}` |
| 三步向导 | GET `/api/v1/onboarding/options`；POST `/api/v1/onboarding/check`、`/api/v1/onboarding/evaluations` |
| 缺陷闭环 | `/api/v1/quality/issues`，支持自动异常、批次导入、批量处理、适用范围、发起/核验同 Agent 复测及回归沉淀 |
| 发布建议及报告 | POST/GET `/api/v1/release-decisions`；GET `/api/v1/release-decisions/{id}/export` |
| 实际支持能力 | GET `/api/v1/capabilities` |
| 个人会话 | `/api/v1/auth/options`、`login`、`session`、`logout`、`sessions` |
| 团队与项目成员 | `/api/v1/team/users`、`/api/v1/team/members` |
| 机器令牌与审计 | `/api/v1/team/machines`、`/api/v1/team/audit`（管理员） |
| HTTP 凭据与测试调用 | `/api/v1/team/credentials`；POST `/api/v1/onboarding/test-call` |
| 证据保护 | `/api/v1/protection/policy/{project_id}`、`grants/{project_id}`、`storage/{project_id}` |
| 受审计原文 | POST `/api/v1/protection/evaluations/{run_id}/raw`，个人专项资格及必填理由 |
| 预览与复用 | POST `/api/v1/onboarding/preview`、`reuse-preview`、`reuse`；GET `/api/v1/evaluations/{id}/reuse-config` |
| 提交查询与草稿 | GET `/api/v1/onboarding/requests/{key}?project_id=…`；GET/POST `/api/v1/onboarding/drafts/{project_id}` |
| 用例预检与差异 | POST `/api/v1/benchmarks/{id}/preview`、`publish`；GET `/api/v1/benchmark-versions/{id}/diff?other_version_id=…` |
| 显式基线 | GET/POST `/api/v1/quality/baselines/{project_id}` |

列表当前使用 offset/limit。发布版本无更新接口。创建 Run 支持 `Idempotency-Key` 请求头；相同键和请求返回原 Run，不同请求返回 409。报告返回实际 `grading_batch_id`；历史查询应显式传入该 ID。

## Docker Compose

已提供前端多阶段构建 + API + PostgreSQL + Redis + Worker + Dispatcher 配置。复制 `.env.example` 为 `.env`，修改 API token 和数据库密码，再执行：

```bash
docker compose up --build
```

数据库密码使用 URL 安全随机字符，避免连接 URL 转义问题。API 仅映射到本机；数据库不暴露宿主端口。Redis 同样不暴露宿主端口；Worker 与 Dispatcher 等待 API 迁移及健康检查完成后启动。

当前包的完整隔离演练由新工具负责，自动构建当前固定提交的前后端镜像和五个服务；验证重启排队、Worker SIGKILL、数据库/Redis 中断、真实团队鉴权、全库和环境卷恢复、固定报告及兼容旧包回滚。使用未被占用的新项目名，无需设置测试密钥：

```powershell
.venv/Scripts/python.exe scripts/deployment_acceptance.py --project ae-local-012-acceptance --rollback-ref 61a71dab8ca82ca5ad328f9fca0146f2152fb12e
```

工具不读取本机 `.env`，自动生成临时凭据，API 只绑定随机本机端口；不接管已有同名容器或数据卷。Windows 中文路径自动通过固定 Git 归档暂存构建。结束后停止验收栈并保留数据卷和 `.build/<项目>-<时间>/` 备份、摘要报告；报告不含凭据，但全库/环境备份须按敏感数据管理。不要上传备份正文。

工作树需先提交，旧包需和当前数据库结构兼容；未传 `--rollback-ref` 时报告明确记为未演练回滚。独立 [Deployment Recovery workflow](.github/workflows/deployment-acceptance.yml) 手动触发，不在每次 push 隐式执行故障演练。步骤和限制见 [0.12 运维交付](docs/V0.12_OPERATIONS.md)。旧 `scripts/compose_acceptance.py` 仅保留 0.5.1 排队恢复流程，其历史成果不代表当前包验收。

## 独立队列与新接入

配置 `AGENTEVAL_EXECUTION_MODE=celery` 与 `AGENTEVAL_BROKER_URL`，保持 API、Worker、Dispatcher 使用相同数据库和配置。先迁移，再分别启动：

```powershell
$env:PYTHONPATH = 'backend'
.venv/Scripts/python.exe -m agenteval init-db
.venv/Scripts/python.exe -m agenteval serve
# 另外两个终端，同样设置执行模式、数据库与 Broker 环境变量：
.venv/Scripts/python.exe -m agenteval worker
.venv/Scripts/python.exe -m agenteval dispatch
```

Worker 默认使用 solo 池，每个进程执行一个工作项，可启动多个 Worker；跨进程的 Project/Run 并发上限由数据库事务维护。PostgreSQL 是 Compose 的部署数据库，SQLite 主要用于本地验证。

LangGraph 注册时选择 `adapter_type=langgraph`、`plugin_id=example.order.graph`，声明对应观测能力。部署方可扩展可信 `GRAPH_PLUGINS` 与图工厂。适配器消费实际编译图的 updates/values 流，不执行请求提供的模块路径。

Judge 使用服务端环境变量：

```text
AGENTEVAL_JUDGE_ENDPOINT=<完整 chat/completions 兼容端点>
AGENTEVAL_JUDGE_MODEL=<模型标识>
AGENTEVAL_JUDGE_API_KEY=<本机环境或部署 Secret 中的密钥>
AGENTEVAL_JUDGE_MAX_CALLS=100
```

Profile 添加 `llm_judge`，配置 `rubric` 和可选 `timeout_seconds`；也可在工作台选择“使用规则 + Judge”。端点需支持 `response_format.type=json_schema`，模型和端点在创建 Run 时冻结。密钥不进入数据库、Profile 或 Manifest。没有配置端点/模型时，创建任务会被预检拒绝。

Judge 每个评分项最多两次请求，只对传输错误与指定暂时状态重试；无效结构标记评分异常，拒答或证据不足标记待复核。每个 Run 的持久化调用预算也包含重评与中断请求。当前以调用数控制预算，尚未实现精确费用预算。

组合验证：`python scripts/queue_smoke.py` 启动真实 Celery 进程，默认使用显式标注的 fakeredis 协议模拟及裁判模拟。设置 `AGENTEVAL_TEST_BROKER_URL` 后使用真实 Redis；CI 同时使用真实 Redis 和 PostgreSQL。测试不调用付费模型，不能作为实际 Judge 准确率证明。

现有工作台与报告能力：

- 工作台列表使用服务端分页和批量版本关联查询，不再每次轮询拉取所有资源；任务用例表分页，评分证据按执行 ID 获取。
- “运行健康”页面区分存活与就绪，展示 API 维护循环、Worker/Dispatcher 心跳、积压和 blocked；临时异常进入退避恢复。
- Judge 上限在 Run 创建时写入 Manifest 和独立预算记录，环境变量变化不改变已创建 Run；旧运行首次使用时固定，并明确标注来源。
- “查看证据”展示 OTel 树、时间线与事件。未结束 Span 显示“未结束”，截断/缺失观测会标记 partial/wrapper_only，不补造内部步骤。
- `tool_call` 按唯一 call_id 检查同名多次调用、参数 Schema/字段值、结果 Schema/期望值、先后依赖和声明式结果使用规则。默认不允许未声明的额外调用。
- “组件评测”直接启动 Retriever/Reranker，无需注册业务 Agent；也可创建样例数据与评分方案体验。
- 报告导出必须指定评分批次；CSV 处理公式注入，HTML 转义内容，JSON 提供内容哈希。导出按字段名做基础脱敏，业务文本仍需项目方审核。

详细接口与验收说明见 [0.4 交付说明](docs/V0.4_ACCEPTANCE.md)。更多运行与恢复约定见 [M2 执行与 Judge 说明](docs/M2_EXECUTION_AND_JUDGE.md)。

## 证据、故障与评分口径

`Execution.evidence_provenance` 由平台写入，区分 Agent 自述、埋点数据与独立环境观察；埋点存在不等于业务状态独立验证。环境快照必须先持久化再重置，崩溃后保留已提交终态；重置完成但确认未落库时仍记 pending，不猜测为成功。状态来源或内容哈希校验失败会产生评分异常。

逐调用工具评分独立处理通过、失败、证据不足，缺失证据不污染其他调用；任何必需调用的确定失败都会使该评分项得到 0 分。部分观测中的调用缺失不能证明未执行。第三方 ID 可显式映射，按同名出现次序匹配仅适用于完整且顺序可靠的通道。

Profile 的 `execution.tool_faults` 可指定 `tool_name`、`call_index`、`mode`（mock/error/timeout）、`mock_output` 和 `delay_seconds`。注入规则随 Manifest 冻结，证据保留 platform_profile 标记；黑盒 HTTP/组件不支持此类注入。并发序号表示本次实际入口顺序，跨运行对齐应使用稳定 call_id。

报告的 `trial_scoring_coverage` 使用 canonical Execution 的已评分项除以计划 Trial × 评分器实例数；`attempt_scoring_coverage` 使用所有尝试的已评分项除以 Attempt × 实例数。旧 `scoring_coverage` 是 Attempt 别名，覆盖率门禁使用 Trial 口径。重复试验成功率同时提供覆盖率，缺失证据不算零分。严格比较包含环境适配器配置，不兼容时拒绝；探索比较只披露差异，不给通过结论。

真实 Judge 校准入口为 `scripts/calibrate_judge.py`，例如 `python scripts/calibrate_judge.py --output .build/judge-calibration.json --repetitions 3`。需要实际端点/模型配置；仓库种子标签尚待独立人工复核，模拟协议和离线分析不能替代真实模型一致性报告。

## 升级到 0.11

停止旧 API/Worker/Dispatcher，确认无在途调用并备份数据库与部署秘密，在副本演练后运行 `python -m agenteval init-db` 至 **0010**，前后端同步升级到 0.11。新增显式基线与个人草稿表，不回写历史评测；已有项目基线默认未配置，由用户选择固定评分批次后保存。个人草稿需个人账户和已选项目，不保存到浏览器持久存储。归档升级为 v2，旧 v1 可验证并恢复到升级后的隔离空业务库；新包不能用于旧程序回退。详细流程及兼容限制见 [0.11 交付](docs/V0.11_PRODUCT_COMPLETION.md)，个人账户初始化见 [0.9](docs/V0.9_TEAM_FOUNDATION.md)。

## 当前使用边界

当前面向受控内部团队，team 模式提供项目角色隔离；完整数据保护与真实业务上线条件仍未齐备。local 与 celery 模式采用相同数据库工作项、租约和结果保护协议；API 重启不撤销有效 Worker 租约。租约过期且结果未确认时标记 unknown，未知副作用不自动重跑，并保留容量待管理员核验。取消可终止本地插件进程，但中断 HTTP 等待不代表远端已停止。

已使用 OpenTelemetry SDK 记录本平台埋点范围内的父子 Span、事件、输入输出和耗时，并通过增量 IPC 保留失败或超时前的轨迹；黑盒内部未提供的观测不生成。已有可配置展示脱敏和个人原文权限，但不能自动发现任意业务秘密，仍应使用脱敏测试数据并验证项目策略。归档为签名原始项目快照，不含加密对象存储或自动清理。HTTP 主机允许清单尚未实现 DNS/IP 固定校验和基础设施出站隔离。Python 子进程仅运行部署注册的可信插件，不能作为不可信代码沙箱。

## 文档维护

每轮构建同步更新本 README 的版本、交付能力、验证记录和未完成边界，并与 BUILD_STATUS、对应验收说明保持一致。历史验证按版本保留，不以模拟服务代替真实验收，也不将旧版本部署结果记作新版本重新验收。


## 团队版批次 A：任务查询与证据

任务列表按项目在服务端查询全部记录，支持 `q`（任务 ID、Agent、Benchmark）、`status`、`quality`、`version`、`created_after`/`created_before`、`sort` 以及 `offset`/`limit`。查询条件存入 URL，返回列表保持筛选；任务发起人没有个人身份时明确显示共享团队身份。完整参数、质量结论及统计边界见 [0.8 合同](docs/V0.8_TEAM_BATCH_A.md)。

```python
from sdk.client import AgentEvalClient

with AgentEvalClient(base_url, deployment_token) as client:
    page = client.list_evaluations(project_id, quality="failed", q="订单", limit=10)
```

固定批次报告新增 `quality_summary` 和 P95 样本阈值说明。证据详情可查看规则事实、预期/实际路径差异和来源 Trace；高级 JSON 与哈希仍可展开。此摘要不修改历史数据，也不取代开发者确认根因。未知结果即便没有详细原因，也保留待判定语义。
