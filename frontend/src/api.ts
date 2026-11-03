import type { Page } from './types';

export class APIError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const messages: Record<string, string> = {
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
  return { get: <T,>(path: string) => request<T>(path), post: <T,>(path: string, body?: unknown) => request<T>(path, body, 'POST'), all };
}
export type API = ReturnType<typeof createAPI>;

