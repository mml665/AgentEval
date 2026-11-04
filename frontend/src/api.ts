import type { Page } from './types';

export class APIError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const messages: Record<string, string> = {
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

export function createAPI(token: string, unauthorized: () => void) {
  async function request<T>(path: string, body?: unknown, method = 'GET'): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`/api/v1/${path}`, {
        method, headers: { Authorization: `Bearer ${token}`, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
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
    const response = await fetch(`/api/v1/${path}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) { if (response.status === 401) unauthorized(); throw new APIError(response.status, '导出失败，请确认评分批次已完成。'); }
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url;
    anchor.download = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'agenteval-report.json';
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return { download, get: <T,>(path: string) => request<T>(path), post: <T,>(path: string, body?: unknown) => request<T>(path, body, 'POST'), all };
}
export type API = ReturnType<typeof createAPI>;

