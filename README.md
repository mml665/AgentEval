# AgentEval

面向异构 Agent 的自动化评测与回归分析平台。

**当前版本：0.2.0 / M1 核心闭环 + Web 工作台。** 已实现 React/TypeScript 前端、可运行的 API、版本化数据、HTTP/Python 执行、四粒度代表性规则评分、独立评分批次和基础回归门禁。完整 MVP 仍在构建，当前不包含 Celery/Redis、LangGraph 或 LLM Judge。

- [项目 PRD](docs/AgentEval_PRD_V1.1.md)
- [系统设计](docs/AgentEval_系统详细设计_V1.1.md)
- [实际构建状态、限制与验证记录](docs/BUILD_STATUS.md)

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

必须以**单 API 进程**运行。首次启动前执行迁移；应用启动不会隐式建表。数据库文件位于 `data/agenteval.db`，不会进入 Git。

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

示例会创建项目、两个 Agent 版本、两条用例及五个评分项。它验证以下真实结果：

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
```

测试包括真实回环 HTTP 调用、Python 子进程超时、评分证据缺失、历史重评、幂等创建、配置不可比拒绝及空库迁移。`scripts/smoke.py` 自动启动临时 API 和 HTTP Agent，通过 SDK 执行完整回归，最后关闭进程；临时数据库和证据位于被 Git 忽略的 `.build/`。

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
| 执行 | POST/GET `/api/v1/evaluations` |
| 状态与批次 | GET `/api/v1/evaluations/{id}` |
| 报告/评分/失败项 | GET `/api/v1/evaluations/{id}/report`、`results`、`bad-cases` |
| 重评 | POST `/api/v1/evaluations/{id}/regrade` |
| 执行与观测 | GET `/api/v1/executions/{id}`、`/api/v1/traces/{trace_id}` |
| 回归 | POST `/api/v1/regressions/compare` |
| 实际支持能力 | GET `/api/v1/capabilities` |

列表当前使用 offset/limit。发布版本无更新接口。创建 Run 支持 `Idempotency-Key` 请求头；相同键和请求返回原 Run，不同请求返回 409。报告返回实际 `grading_batch_id`；历史查询应显式传入该 ID。

## Docker Compose

已提供前端多阶段构建 + API + PostgreSQL 配置。复制 `.env.example` 为 `.env`，修改 API token 和数据库密码，再执行：

```bash
docker compose up --build
```

数据库密码使用 URL 安全随机字符，避免连接 URL 转义问题。API 仅映射到本机；数据库不暴露宿主端口。Compose 不包含尚未实现的 Worker/Redis。

本机仅验证 Compose 配置解析，容器构建与运行尚未验证。PostgreSQL 迁移和前后端校验交由仓库 CI 执行，其实际状态应以 Actions 结果为准。

## 当前使用边界

M1 面向单可信团队的本地示例和开发验证。采用进程内调度，缺少耐久队列与分布式租约；进程重启会将中断运行标记失败、未确认调用标记未知，不自动重执行。取消停止后续派发，已开始的调用等待完成或超时，不保证远端停止。

当前观测为内联结构化通道，尚不是完整 OTel Trace。输入输出尚未实现业务敏感数据的完整脱敏、制品存储和留存清理，请使用脱敏测试数据。HTTP 主机允许清单尚未实现 DNS/IP 固定校验和基础设施出站隔离。Python 子进程仅运行部署注册的可信插件，不能作为不可信代码沙箱。
