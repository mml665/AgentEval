import type { Page } from './types';

export class APIError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const messages: Record<string, string> = {
  CASE_DOCUMENT_INVALID: '用例预检失败，请按下方行号、case_key 和字段位置修复；未发布任何版本。',
  BENCHMARK_IDENTITY_MISMATCH: '只能比较同一 Benchmark 身份下的两个固定版本。',
  BASELINE_REVISION_CONFLICT: '显式基线已被更新，请刷新范围后再保存。',
  DRAFT_REVISION_CONFLICT: '个人草稿已被其他页面更新，请先读取最新修订，再选择使用哪份配置。',
  PERSONAL_DRAFT_REQUIRED: '服务端草稿仅供个人账户使用，按成员和项目隔离。',
  RAW_EVIDENCE_PERMISSION_REQUIRED: '原始证据需要个人管理员或项目专项授权；共享和机器令牌不能读取。',
  REDACTION_SECRET_UNAVAILABLE: '服务端缺少内容脱敏变量，请由部署管理员配置后再发布策略。',
  REDACTION_POLICY_UNAVAILABLE: '脱敏变量缺失或原值已变更，服务端已停止返回业务视图。请管理员恢复配置或发布新的策略版本。',
  PROTECTION_REVISION_CONFLICT: '保护策略已被其他管理员更新，请刷新后重新编辑。',
  DISPLAY_RESPONSE_TOO_LARGE: '结果超过单次安全展示上限，请分页查询证据。',
  LOGIN_REJECTED: '用户名或密码无效，或成员已停用。',
  LOGIN_RATE_LIMITED: '登录尝试过于频繁，请稍后重试。',
  SESSION_EXPIRED: '会话已过期，请重新登录。',
  SESSION_REVOKED: '会话或账号已撤销，请重新登录。',
  CSRF_REJECTED: '安全校验未通过，请刷新会话后重试。',
  ORIGIN_NOT_ALLOWED: '当前页面来源未获部署许可，请检查会话 Origin 配置。',
  READ_ONLY: '当前项目为只读权限，无法执行写操作。',
  ADMIN_REQUIRED: '此操作需要部署管理员权限。',
  PROJECT_SCOPE_REQUIRED: '请选择有权限的项目；只有管理员可创建新项目。',
  ASSIGNEE_REFERENCE_REQUIRED: '请选择项目成员，不能用自由文本代替真实成员身份。',
  ASSIGNEE_NOT_ACTIVE_MEMBER: '负责人必须是当前项目的有效成员。',
  CREDENTIAL_PROVIDER_NOT_CONFIGURED: '服务端尚未配置凭据指纹密钥，请由部署管理员配置后重试。',
  CREDENTIAL_UNAVAILABLE: '凭据引用未能解析，请检查服务端环境变量及合法 Header 值。',
  CREDENTIAL_REVOKED: '凭据版本已撤销，请登记新版本并发布新的 Agent 版本。',
  CREDENTIAL_VERSION_CHANGED: '环境秘密已轮换，请登记新的凭据版本；旧引用不会自动采用新秘密。',
  CREDENTIAL_SCOPE_MISMATCH: '凭据版本不属于当前项目。',
  LAST_ADMIN_REQUIRED: '至少保留一名有效部署管理员。',

  INVALID_TIME_RANGE: '起始日期不能晚于结束日期，请调整筛选条件。',
  VALIDATION_ERROR: '参数校验失败，请核对下方字段位置。',
  INCOMPLETE_EVALUATION_SCOPE: '请选择 Agent、候选版本和 Benchmark 固定版本。',
  EVALUATION_SCOPE_MISMATCH: '所选版本必须属于当前 Agent，Benchmark 必须属于当前项目。复测应沿用同一 Agent 与 Benchmark 身份。',
  ANOMALY_TRIAGE_ONLY: '仅可批量确认或忽略待确认异常，请刷新并核对所选记录。',
  MERGE_SCOPE_MISMATCH: '仅能合并相同 Agent、Benchmark 和用例内容的记录。',
  INVALID_MERGE: '请至少选择两条尚未关闭、忽略或合并的记录。',
  INACTIVE_ISSUE_SCOPE: '已关闭、忽略或合并的记录不能直接修改适用版本。',
  ENGINE_REASSESSMENT_REQUIRED: '该证据来自较旧引擎。请先在评测详情重新评分，再导入当前批次或建立新的版本比较。',
  ISSUE_REVISION_CONFLICT: '问题已被更新，请刷新后再保存。',
  INVALID_ISSUE_TRANSITION: '请按确认、修复、待复测的顺序处理；关闭状态由复测证据决定。',
  ISSUE_NOT_READY_OR_STALE: '问题尚未进入待复测，或记录已变化，请刷新并核对状态。',
  RETEST_POLICY_MISMATCH: '复测的评分、执行、环境、裁判配置或引擎版本与来源不一致，不能直接认定修复。',
  RETEST_CASE_MISMATCH: '复测没有包含相同输入和预期断言的用例，不能认定修复。',
  INVALID_RETEST_BATCH: '请选择同一项目的新评分批次，不能用原失败批次确认修复。',
  IDEMPOTENCY_CONFLICT: '请求标识已用于另一组配置，请返回修改后重新提交。',
  JUDGE_NOT_CONFIGURED: '服务端尚未配置 Judge 接口和模型，请先设置裁判环境变量。',
  JUDGE_BUDGET_EXCEEDED: '计划的 Judge 调用数超过单 Run 预算。',
  RETRY_REQUIRES_READ_ONLY: '自动重试仅适用于显式声明为只读的 Agent 版本。',
  RESOURCE_CONFLICT: '该名称或版本已存在，请使用新的版本号。',
  UNAUTHORIZED: '访问令牌无效，请重新连接。',
  INCOMPARABLE: '两个运行的评测条件不一致，无法严格比较。可选择探索模式查看差异。',
  PREFLIGHT_FAILED: '预检失败：请检查 Agent 能力与用例断言是否满足评分方案。',
  PROJECT_MISMATCH: 'Agent、数据集和评分方案必须属于同一项目。',
  RUN_NOT_SETTLED: '运行尚未结束，暂时不能重新评分。',
  GRADING_IN_PROGRESS: '已有评分批次正在运行，请稍后重试。',
  RESULTS_NOT_FROZEN: '请等待运行和评分全部结束后再比较。',
  UNKNOWN_CORE_CASE: '核心用例不存在于当前数据集版本中。',
};

export function createAPI(token: string, unauthorized: () => void, csrf = '') {
  async function request<T>(path: string, body?: unknown, method = 'GET'): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`/api/v1/${path}`, {
        method, credentials: 'same-origin', headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(csrf && method !== 'GET' ? { 'X-CSRF-Token': csrf } : {}), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new APIError(0, '无法连接服务，请检查后端是否已启动。'); }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) unauthorized();
      const detail = data.detail || data;
      const code = detail.code || data.code;
      const issues = detail.errors || data.details;
      const extra = issues ? `\n${JSON.stringify(issues, null, 2)}` : '';
      throw new APIError(response.status, (messages[code] || detail.message || (typeof detail === 'string' ? detail : `请求失败（${response.status}）`)) + extra);
    }
    return response.json() as Promise<T>;
  }
  async function all<T>(path: string): Promise<T[]> {
    const items: T[] = [];
    let offset = 0;
    while (true) {
      const page = await request<Page<T>>(`${path}${path.includes('?') ? '&' : '?'}limit=100&offset=${offset}`);
      items.push(...page.items);
      if (page.items.length < 100 || (page.total !== undefined && items.length >= page.total)) return items;
      offset += 100;
      if (offset >= 10000) throw new APIError(0, '当前项目数据超过工作台加载上限，请通过 API 分页查询。');
    }
  }
  async function download(path: string) {
    const response = await fetch(`/api/v1/${path}`, { credentials: 'same-origin', headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!response.ok) { if (response.status === 401) unauthorized(); throw new APIError(response.status, '导出失败，请确认评分批次已完成。'); }
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url;
    anchor.download = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'agenteval-report.json';
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return { download, get: <T,>(path: string) => request<T>(path), post: <T,>(path: string, body?: unknown) => request<T>(path, body, 'POST'), all };
}
export type API = ReturnType<typeof createAPI>;

