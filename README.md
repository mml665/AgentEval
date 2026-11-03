# AgentEval

面向异构 Agent 的自动化评测与回归分析平台。

**当前版本：0.5.2。** 在 0.4 执行与 Trace 闭环上，新增独立环境状态验证、轨迹规则、工具证据与 ID 映射、评分器三层元数据、专项指标及性能门禁、Judge 标签校准流程、独立 FTS5 Retriever / TF-IDF Reranker 与 HTTP 接入。真实业务 Agent 的通用性、实际模型校准和完整生产验收仍待验证。

- [项目 PRD](docs/AgentEval_PRD_V1.1.md)
- [系统设计](docs/AgentEval_系统详细设计_V1.1.md)
- [实际构建状态、限制与验证记录](docs/BUILD_STATUS.md)
- [0.5.2 证据与恢复边界](docs/V0.5.2_HARDENING.md)
- [0.5.1 核心修复与待验收项](docs/V0.5.1_CORE_ACCEPTANCE.md)
- [0.5 使用契约与验收边界](docs/V0.5_ACCEPTANCE.md)

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

API 默认监听 `127.0.0.1:8000`。除了 `/health`，业务接口均需 `Authorization: Bearer <token>`。受保护的 OpenAPI 文档位于 `/api/v1/openapi.json`；本轮未提供 Swagger 页面。`.env.example` 是配置参考，本地命令不自动加载 `.env`，应使用环境变量。

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

页面包括：评测概览、Agent 注册和版本发布、Benchmark JSON/JSONL 导入、评分方案、任务创建和轮询、评分批次选择、执行证据、回归比较与门禁。空项目显示配置引导，可点击“创建演示项目”生成真实持久化示例并执行两次评测。

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
| 实际支持能力 | GET `/api/v1/capabilities` |

列表当前使用 offset/limit。发布版本无更新接口。创建 Run 支持 `Idempotency-Key` 请求头；相同键和请求返回原 Run，不同请求返回 409。报告返回实际 `grading_batch_id`；历史查询应显式传入该 ID。

## Docker Compose

已提供前端多阶段构建 + API + PostgreSQL + Redis + Worker + Dispatcher 配置。复制 `.env.example` 为 `.env`，修改 API token 和数据库密码，再执行：

```bash
docker compose up --build
```

数据库密码使用 URL 安全随机字符，避免连接 URL 转义问题。API 仅映射到本机；数据库不暴露宿主端口。Redis 同样不暴露宿主端口；Worker 与 Dispatcher 等待 API 迁移及健康检查完成后启动。

本机仅验证 Compose 配置解析，容器构建与运行尚未验证。PostgreSQL 迁移和前后端校验交由仓库 CI 执行，其实际状态应以 Actions 结果为准。

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

0.4 新功能：

- 工作台列表使用服务端分页和批量版本关联查询，不再每次轮询拉取所有资源；任务用例表分页，评分证据按执行 ID 获取。
- “运行健康”页面区分存活与就绪，展示 API 维护循环、Worker/Dispatcher 心跳、积压和 blocked；临时异常进入退避恢复。
- Judge 上限在 Run 创建时写入 Manifest 和独立预算记录，环境变量变化不改变已创建 Run；旧运行首次使用时固定，并明确标注来源。
- “查看证据”展示 OTel 树、时间线与事件。未结束 Span 显示“未结束”，截断/缺失观测会标记 partial/wrapper_only，不补造内部步骤。
- `tool_call` 按唯一 call_id 检查同名多次调用、参数 Schema/字段值、结果 Schema/期望值、先后依赖和声明式结果使用规则。默认不允许未声明的额外调用。
- “组件评测”直接启动 Retriever/Reranker，无需注册业务 Agent；也可创建样例数据与评分方案体验。
- 报告导出必须指定评分批次；CSV 处理公式注入，HTML 转义内容，JSON 提供内容哈希。导出按字段名做基础脱敏，业务文本仍需项目方审核。

详细接口与验收说明见 [0.4 交付说明](docs/V0.4_ACCEPTANCE.md)。更多运行与恢复约定见 [M2 执行与 Judge 说明](docs/M2_EXECUTION_AND_JUDGE.md)。

## 当前使用边界

当前面向单可信团队。local 与 celery 模式采用相同数据库工作项、租约和结果保护协议；API 重启不撤销有效 Worker 租约。租约过期且结果未确认时标记 unknown，未知副作用不自动重跑，并保留容量待管理员核验。取消可终止本地插件进程，但中断 HTTP 等待不代表远端已停止。

已使用 OpenTelemetry SDK 记录本平台埋点范围内的父子 Span、事件、输入输出和耗时，并通过增量 IPC 保留失败或超时前的轨迹；黑盒内部未提供的观测不生成。输入输出尚未实现业务敏感数据的完整脱敏、制品存储和留存清理，请使用脱敏测试数据。HTTP 主机允许清单尚未实现 DNS/IP 固定校验和基础设施出站隔离。Python 子进程仅运行部署注册的可信插件，不能作为不可信代码沙箱。
