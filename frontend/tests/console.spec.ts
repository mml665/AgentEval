import { test, expect, type Page } from '@playwright/test';

const token = 'local-browser-test-token-only';

async function fixture(post: (path:string,body:unknown) => Promise<any>, name:string) {
  const project=await post('projects',{name});
  const agent=await post('agents',{project_id:project.id,name:'Workflow fixture',adapter_type:'python'});
  const av=await post(`agents/${agent.id}/versions`,{version:'1',configuration:{plugin_id:'example.order.v1'}});
  const av2=await post(`agents/${agent.id}/versions`,{version:'2',configuration:{plugin_id:'example.order.v2'}});
  const bm=await post('benchmarks',{project_id:project.id,name:'Fixed cases'});
  const bv=await post(`benchmarks/${bm.id}/versions`,{version:'1',cases:[{case_key:'one',input:{order_id:'1001'},assertions:{expected_output:{order_id:'1001',status:'shipped'}}}]});
  const profile=await post('profile-versions',{project_id:project.id,name:'Rules',version:'1',evaluators:[{instance_id:'answer',id:'exact_match'}]});
  const run=await post('evaluations',{agent_version_id:av.id,benchmark_version_id:bv.id,profile_version_id:profile.id});
  return {project,agent,av,av2,bm,bv,profile,run};
}

async function login(page: Page) {
  await page.goto('/');
  await page.getByLabel('访问令牌', { exact: true }).fill(token);
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeAttached();
}

test('login rejects invalid token, then completes real evaluation, evidence and regression flow', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('访问令牌', { exact: true }).fill('invalid-token-long-enough');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('alert')).toContainText('访问令牌无效');
  await page.getByLabel('访问令牌', { exact: true }).fill(token);
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('heading', { name: /从一个项目开始/ })).toBeVisible();
  await page.getByRole('button', { name: '创建演示项目', exact: true }).click();
  await expect(page.getByRole('heading', { name: '质量工作台', exact: true })).toBeVisible({ timeout: 45000 });
  await expect(page.locator('.metric-card').filter({ hasText: '待处理缺陷' }).locator('strong')).toHaveText('0');
  await expect(page.locator('.metric-card').filter({ hasText: '评测通过率' }).locator('strong')).toHaveText('50%');
  await page.getByRole('link', { name: 'Agent 管理', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Order Assistant', exact: true })).toBeVisible();
  await expect(page.locator('.version-item')).toHaveCount(2);
  await page.getByRole('link', { name: 'Benchmark', exact: true }).click();
  await page.getByRole('button', { name: /v1.0.*2 条用例/ }).click();
  await expect(page.getByRole('dialog')).toContainText('order-1001');
  await page.getByLabel('关闭弹窗').click();
  await page.getByRole('link', { name: '评测任务', exact: false }).click();
  await page.getByRole('link', { name: 'Order Assistant · v2.0', exact: true }).click();
  await expect(page.getByRole('heading', { name: '用例执行结果' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '专项评分与观测覆盖' })).toBeVisible();
  await expect(page.getByText('仅验证输出与声明的过程观测；未独立核验业务环境状态。')).toBeVisible();
  await page.getByLabel('用例结果筛选').selectOption('bad');
  await expect(page.locator('section.panel').filter({ has: page.getByRole('heading', { name: '用例执行结果' }) }).locator('tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: '查看证据' }).click();
  await page.getByRole('dialog').getByText('原始过程观测', { exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('get_order');
  await expect(page.getByRole('dialog')).toContainText('processing');
  await page.getByLabel('关闭弹窗').click();
  await page.getByRole('button', { name: '重新评分' }).click();
  await expect(page.getByLabel('评分批次').locator('option')).toHaveCount(2);
  await page.getByRole('link', { name: '版本回归', exact: true }).click();
  await page.getByRole('button', { name: /^查看比较/ }).click();
  await expect(page.getByRole('heading', { name: '未通过质量门禁' })).toBeVisible();
  await expect(page.locator('.change-card').filter({ hasText: '新增失败' })).toContainText('order-1001');
  await page.screenshot({ path: '../.build/console-regression.png', fullPage: true });
  await page.getByRole('link', { name: '质量工作台', exact: true }).click();
  await page.screenshot({ path: '../.build/console-overview.png', fullPage: true });
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(stored).not.toContain(token);
  expect(errors).toEqual([]);
});

test('manual configuration publishes resources and creates a preflighted evaluation from the task list', async ({ page, request }) => {
  await login(page);
  await page.getByRole('button', { name: '新建项目', exact: true }).first().click();
  const modal = page.getByRole('dialog');
  await modal.getByLabel('项目名称').fill('手动配置项目');
  await modal.getByRole('button', { name: '创建项目' }).click();
  await expect(modal).not.toBeVisible();
  await page.getByRole('link', { name: 'Agent 管理', exact: true }).click();
  await page.getByRole('button', { name: '注册 Agent', exact: true }).click();
  await modal.getByLabel('Agent 名称').fill('手动 Order Agent');
  await modal.getByLabel('接入方式').selectOption('langgraph');
  await modal.getByRole('button', { name: '注册 Agent' }).click();
  await page.getByRole('button', { name: '发布版本', exact: true }).click();
  await modal.getByLabel('版本号').fill('1.0');
  await modal.getByLabel('执行副作用').selectOption('read_only');
  await modal.getByLabel('工具调用与参数').check();
  await modal.getByLabel('工作流步骤').check();
  await modal.getByLabel('检索结果排序').check();
  await modal.getByRole('button', { name: '发布不可变版本' }).click();
  await expect(modal).not.toBeVisible();
  await page.getByRole('link', { name: 'Benchmark', exact: true }).click();
  await page.getByRole('button', { name: '创建数据集' }).click();
  await modal.getByLabel('数据集名称').fill('手动订单用例');
  await modal.getByRole('button', { name: '创建数据集' }).click();
  await page.getByRole('button', { name: '导入新版本' }).click();
  await modal.getByLabel('版本号').fill('1.0');
  await modal.getByRole('button', { name: '填入示例用例' }).click();
  await expect(modal).toContainText('已解析 2 条用例');
  await modal.getByRole('button', { name: '校验并发布版本' }).click();
  await expect(modal).not.toBeVisible();
  await page.getByRole('link', { name: '评分方案', exact: true }).click();
  await page.getByRole('button', { name: '创建方案' }).click();
  await modal.getByLabel('方案名称').fill('手动四粒度评分');
  await modal.getByRole('button', { name: '使用四粒度示例' }).click();
  await modal.getByRole('button', { name: '发布评分方案' }).click();
  await expect(modal).not.toBeVisible();
  const projectId = await page.getByLabel('当前项目').inputValue();
  const headers = { Authorization: `Bearer ${token}` };
  async function post(path: string, data: unknown) {
    const r = await request.post(`/api/v1/${path}`, { headers, data });
    expect(r.ok(), await r.text()).toBeTruthy();
    return r.json();
  }
  for (let i = 0; i < 12; i++) {
    await post('profile-versions', { project_id: projectId, name: `其他方案 ${i}`, version: '1', evaluators: [{ instance_id: 'answer', id: 'exact_match' }] });
    const agent = await post('agents', { project_id: projectId, name: `其他 Agent ${i}`, adapter_type: 'python' });
    await post(`agents/${agent.id}/versions`, { version: '1', configuration: { plugin_id: 'example.order.v1' } });
    const bm = await post('benchmarks', { project_id: projectId, name: `其他 Benchmark ${i}` });
    await post(`benchmarks/${bm.id}/versions`, { version: '1', cases: [{ case_key: 'other', input: {}, assertions: { expected_output: 'other' } }] });
  }
  await page.getByRole('link', { name: '评测任务', exact: false }).click();
  await page.getByRole('button', { name: '创建评测' }).click();
  await expect(page.getByLabel('复用评分方案').locator('option')).toHaveCount(14);
  await page.getByLabel('复用 Agent 版本').selectOption({ label: '手动 Order Agent · v1.0' });
  const benchmark = page.getByLabel('复用 Benchmark 版本');
  const option = benchmark.getByRole('option', { name: /手动订单用例/ });
  await expect(option).toHaveCount(1);
  await benchmark.selectOption(await option.getAttribute('value') || '');
  const scoring = page.getByLabel('复用评分方案');
  await scoring.selectOption(await scoring.getByRole('option', { name: /手动四粒度评分/ }).getAttribute('value') || '');
  await expect(page.getByRole('button', { name: '执行复用评测' })).toBeDisabled();
  await page.getByRole('button', { name: '预检并核对配置' }).click();
  await expect(page.getByRole('heading', { name: '提交前配置确认' })).toBeVisible();
  await page.getByRole('button', { name: '执行复用评测' }).click();
  await expect(page.getByRole('heading', { name: '用例执行结果' })).toBeVisible();
  await expect(page.locator('.metric-card').filter({ hasText: '任务成功率' }).locator('strong')).toHaveText('100%', { timeout: 40000 });
  await expect(page.getByRole('heading', { name: '投递与执行保护' })).toBeVisible();
});

test('mobile login, navigation, empty states and dialogs fit viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.getByLabel('打开导航').click();
  await page.getByRole('link', { name: 'Agent 管理', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Agent 管理' })).toBeVisible();
  await page.getByRole('button', { name: '注册 Agent', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByLabel('关闭弹窗').click();
  await page.screenshot({ path: '../.build/console-mobile.png', fullPage: true });
});

test('health, standalone component, trace timeline, export and pagination work through UI', async ({ page }) => {
  await login(page);
  await page.getByRole('link', { name: '运行健康', exact: true }).click();
  await expect(page.getByRole('heading', { name: '后台服务心跳' })).toBeVisible();
  await expect(page.locator('.metric-card').filter({ hasText: '执行就绪' }).locator('strong')).toHaveText('就绪');
  await page.getByRole('link', { name: '组件评测', exact: true }).click();
  await page.getByRole('button', { name: '运行组件示例' }).first().click();
  await expect(page.getByRole('heading', { name: '用例执行结果' })).toBeVisible();
  await expect(page.locator('.metric-card').filter({ hasText: '任务成功率' }).locator('strong')).toHaveText('100%');
  await page.getByRole('button', { name: '查看证据' }).first().click();
  await expect(page.getByRole('heading', { name: '执行链路与时间线' })).toBeVisible();
  await expect(page.locator('.span-row')).toHaveCount(2);
  await page.locator('.span-row').filter({ hasText: 'example.retriever' }).click();
  await expect(page.locator('.span-detail')).toContainText('component.output');
  await page.screenshot({ path: '../.build/console-trace.png', fullPage: true });
  await page.getByLabel('关闭弹窗').click();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: '导出报告', exact: true }).click()]);
  expect(download.suggestedFilename()).toMatch(/\.json$/);
  const { readFile } = await import('node:fs/promises');
  const exported = JSON.parse(await readFile((await download.path())!, 'utf-8'));
  expect(exported.report.success_rate).toBe(1);
  expect(exported.content_hash).toHaveLength(64);
  const projectId = await page.getByLabel('当前项目').inputValue();
  for (let i = 0; i < 12; i++) {
    const response = await page.request.post('/api/v1/agents', { headers: { Authorization: `Bearer ${token}` }, data: { project_id: projectId, name: `Paged Agent ${i}`, adapter_type: 'python' } });
    expect(response.status()).toBe(201);
  }
  await page.getByRole('link', { name: 'Agent 管理', exact: true }).click();
  await page.getByLabel('刷新数据', { exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Paged Agent 11', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeEnabled();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let signal!: () => void;
  const intercepted = new Promise<void>(resolve => { signal = resolve; });
  const resourcePath = `**/api/v1/workspace/${projectId}?*`;
  await page.route(resourcePath, async route => { signal(); await gate; await route.continue(); });
  const clicking = page.getByLabel('刷新数据', { exact: true }).click();
  await intercepted;
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled();
  await expect(page.getByLabel('分页', { exact: true })).toHaveAttribute('aria-busy', 'true');
  release();
  await clicking;
  await expect(page.getByLabel('刷新数据', { exact: true })).toBeEnabled();
  await expect(page.getByRole('heading', { name: 'Paged Agent 11', exact: true })).toBeVisible();
  await page.unroute(resourcePath);
  await expect(page.locator('.resource-card')).toHaveCount(10);
  const firstPage = await page.locator('.resource-card h2').allTextContents();
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Paged Agent 1', exact: true })).toBeVisible();
  const secondPage = await page.locator('.resource-card h2').allTextContents();
  expect(firstPage.filter(name => secondPage.includes(name))).toHaveLength(0);
  await page.screenshot({ path: '../.build/console-paging.png', fullPage: true });
});

test('independent state evidence rejects a false success claim and is inspectable', async ({ page, request }) => {
  async function post(path: string, data: unknown) {
    const response = await request.post(`/api/v1/${path}`, { data, headers: { Authorization: `Bearer ${token}` } });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const project = await post('projects', { name: '状态验证浏览器验收' });
  const agent = await post('agents', { project_id: project.id, name: '假报成功反例', adapter_type: 'python' });
  const av = await post(`agents/${agent.id}/versions`, { version: '1', configuration: { plugin_id: 'example.refund.liar' } });
  const bm = await post('benchmarks', { project_id: project.id, name: '退款状态' });
  const bv = await post(`benchmarks/${bm.id}/versions`, { version: '1', cases: [{ case_key: 'refund-1', input: { order_id: '1' }, assertions: {
    expected_output: { order_id: '1', status: 'requested' }, state: { checks: [{ path: '/refunds', expected: [{ order_id: '1', status: 'requested' }] }] } } }] });
  const profile = await post('profile-versions', { project_id: project.id, name: '独立状态', version: '1', environment: { adapter_id: 'sqlite.refund.v1' },
    evaluators: [{ instance_id: 'answer', id: 'exact_match' }, { instance_id: 'state', id: 'state_assertion' }] });
  const run = await post('evaluations', { agent_version_id: av.id, benchmark_version_id: bv.id, profile_version_id: profile.id });
  await login(page);
  await page.getByLabel('当前项目', { exact: true }).selectOption(project.id);
  await page.getByRole('link', { name: '评测任务', exact: true }).click();
  await page.getByRole('link', { name: `查看任务 ${run.id.slice(0,8)}`, exact: true }).click();
  await expect(page.getByText('已使用独立环境状态快照评分；快照在环境重置前保存。')).toBeVisible();
  const table = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: '专项评分与观测覆盖' }) });
  await expect(table.locator('tr').filter({ hasText: 'state_assertion' })).toContainText('0%');
  await expect(table.locator('tr').filter({ hasText: 'exact_match' })).toContainText('100%');
  await page.getByRole('button', { name: '查看证据' }).click();
  await expect(page.getByRole('dialog')).toContainText('答案符合预期，但独立状态未出现预期退款记录。');
  await page.getByRole('dialog').getByText('最终输出与独立环境快照（原始数据）', { exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('independent_environment_adapter');
  await expect(page.getByRole('dialog')).toContainText('verified');
  await page.screenshot({ path: '../.build/console-state-proof.png', fullPage: true });
});

test('guided evaluation diagnoses false success, tracks repair, verifies retest and saves release decision', async ({ page, request }) => {
  const browserErrors: string[] = [];
  page.on('pageerror', e => browserErrors.push(e.message));
  async function post(path: string, data: unknown) {
    const response = await request.post(`/api/v1/${path}`, { data, headers: { Authorization: `Bearer ${token}` } });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  await login(page);
  await page.getByRole('link', { name: '开始评测', exact: true }).click();
  await page.getByLabel('Agent 名称', { exact: true }).fill('向导退款反例');
  await page.getByLabel('部署插件').selectOption('example.refund.liar');
  await page.getByRole('button', { name: '校验接入并继续' }).click();
  await expect(page.getByText('部署插件已注册；观测能力将在首次执行中核验。')).toBeVisible();
  await page.getByRole('checkbox', { name: /独立状态验证/ }).check();
  await page.getByLabel('独立环境适配器').selectOption('sqlite.refund.v1');
  await page.getByRole('button', { name: '下一步：准备测试用例' }).click();
  await page.getByRole('button', { name: '填入退款演示用例' }).click();
  await page.getByRole('button', { name: '预检并核对配置' }).click();
  await expect(page.getByRole('heading', { name: '提交前配置确认' })).toBeVisible();
  await page.getByRole('button', { name: '执行评测并查看分析' }).click();
  await expect(page.getByRole('heading', { name: '用例执行结果' })).toBeVisible();
  const runId = new URL(page.url()).hash.split('?')[0].split('/')[2];
  const runResponse = await request.get(`/api/v1/evaluations/${runId}`, { headers: { Authorization: `Bearer ${token}` } });
  const source = await runResponse.json();
  const allVersions = await (await request.get(`/api/v1/quality/agent-versions?project_id=${source.project_id}`, { headers: { Authorization: `Bearer ${token}` } })).json();
  const original = allVersions.items.find((v: { id: string }) => v.id === source.agent_version_id);
  const av = await post(`agents/${original.agent_id}/versions`, { version: 'fixed', configuration: { plugin_id: 'example.refund.python' } });
  await page.getByRole('button', { name: '跟踪本批次异常' }).click();
  await expect(page.getByRole('heading', { name: 'Bad Case 管理', exact: true })).toBeVisible();
  await page.locator('tbody tr').filter({ hasText: 'refund-1001' }).getByRole('link', { name: '分析与处理' }).click();
  await expect(page.locator('.page-heading p')).toContainText('输出与业务状态不一致');
  await expect(page.getByText('候选排查方向 · 待开发者确认')).toBeVisible();
  await page.getByLabel('负责人', { exact: true }).fill('验收开发者');
  await page.getByLabel('开发者确认的根因').fill('已确认 Agent 只生成消息，未执行退款写入。');
  for (const [status, label] of [['confirmed', '已确认'], ['fix_in_progress', '修复中'], ['ready_for_retest', '待复测']]) {
    await page.getByLabel('处理状态').selectOption(status);
    await page.getByLabel('本次处理说明').fill(`验收：${label}`);
    await page.getByRole('button', { name: '保存处理记录' }).click();
    await expect(page.locator('.page-heading p')).toContainText(label);
  }
  await page.getByLabel('修复后的 Agent 版本').selectOption(av.id);
  await page.getByRole('button', { name: '开始复测', exact: true }).click();
  await expect(page.getByRole('link', { name: '查看复测任务', exact: true })).toBeVisible();
  const retestId = (await page.getByRole('link', { name: '查看复测任务', exact: true }).getAttribute('href'))!.split('/')[2];
  await expect.poll(async () => { const r = await request.get(`/api/v1/evaluations/${retestId}`, { headers: { Authorization: `Bearer ${token}` } }); return (await r.json()).status; }).toBe('completed');
  await page.getByRole('button', { name: '核验复测并更新状态' }).click();
  await expect(page.locator('.page-heading p')).toContainText('复测已通过');
  await page.getByRole('button', { name: '加入长期回归集', exact: true }).click();
  await expect(page.getByRole('button', { name: '已加入长期回归集', exact: true })).toBeDisabled();
  await page.screenshot({ path: '../.build/product-issue-resolved.png', fullPage: true });
  const retest = await (await request.get(`/api/v1/evaluations/${retestId}`, { headers: { Authorization: `Bearer ${token}` } })).json();
  const comparison = await post('regressions/compare', { baseline_batch_id: source.grading_batches[0].id, candidate_batch_id: retest.grading_batches[0].id });
  await page.getByRole('link', { name: '发布决策', exact: true }).click();
  await page.getByLabel('候选版本', { exact: true }).selectOption(av.id);
  await page.getByLabel('基线版本', { exact: true }).selectOption(source.agent_version_id);
  await page.getByLabel('选择固定版本比较').selectOption(comparison.id);
  await page.getByLabel('评估说明（可选）').fill('固定证据通过；修复已复测。');
  await page.getByRole('button', { name: '生成固定发布评估' }).click();
  await expect(page.getByRole('heading', { name: '当前评测范围满足门禁', exact: true })).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: '导出发布报告 JSON' }).click()]);
  const { readFile } = await import('node:fs/promises');
  const exported = JSON.parse(await readFile((await download.path())!, 'utf-8'));
  expect(exported.status).toBe('ready');
  expect(exported.snapshot.evidence_complete).toBe(true);
  await page.screenshot({ path: '../.build/product-release-ready.png', fullPage: true });
  await page.getByRole('link', { name: '质量工作台', exact: true }).click();
  await expect(page.locator('.metric-card').filter({ hasText: '评测通过率' }).locator('strong')).toHaveText('100%');
  await page.screenshot({ path: '../.build/product-workbench.png', fullPage: true });
  expect(browserErrors).toEqual([]);
});

test('guided setup fits mobile viewport without requiring resource terminology', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.getByLabel('打开导航').click();
  await page.getByRole('link', { name: '开始评测', exact: true }).click();
  await expect(page.getByRole('heading', { name: '三步开始评测' })).toBeVisible();
  await expect.poll(() => page.locator('aside.sidebar').evaluate(e => e.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: '../.build/product-mobile-guide.png', fullPage: true });
});

test('shared Benchmark isolates two Agents, version scopes, anomalies and release decisions', async ({ page, request }) => {
  const headers = { Authorization: `Bearer ${token}` };
  async function post(path: string, data: unknown) {
    const response = await request.post(`/api/v1/${path}`, { data, headers });
    expect(response.ok(), await response.text()).toBeTruthy(); return response.json();
  }
  const project = await post('projects', { name: '双 Agent 范围隔离验收' });
  const agentA = await post('agents', { project_id: project.id, name: 'Agent A', adapter_type: 'python' });
  const oldA = await post(`agents/${agentA.id}/versions`, { version: 'old', configuration: { plugin_id: 'example.refund.liar' } });
  const newA = await post(`agents/${agentA.id}/versions`, { version: 'fixed', configuration: { plugin_id: 'example.refund.python' } });
  const agentB = await post('agents', { project_id: project.id, name: 'Agent B', adapter_type: 'langgraph' });
  const verB = await post(`agents/${agentB.id}/versions`, { version: 'independent', configuration: { plugin_id: 'example.refund.graph' } });
  const bm = await post('benchmarks', { project_id: project.id, name: '共享退款用例' });
  const bv = await post(`benchmarks/${bm.id}/versions`, { version: '1', cases: [{ case_key: 'refund-shared', input: { order_id: '1' }, assertions: {
    expected_output: { order_id: '1', status: 'requested' }, state: { checks: [{ path: '/refunds', expected: [{ order_id: '1', status: 'requested' }] }] } } }] });
  const profile = await post('profile-versions', { project_id: project.id, name: '共享策略', version: '1', environment: { adapter_id: 'sqlite.refund.v1' },
    evaluators: [{ instance_id: 'answer', id: 'exact_match' }, { instance_id: 'state', id: 'state_assertion' }] });
  async function run(id: string) {
    const r = await post('evaluations', { agent_version_id: id, benchmark_version_id: bv.id, profile_version_id: profile.id });
    let completed: { grading_batches: { id: string }[] };
    await expect.poll(async () => { const result = await (await request.get(`/api/v1/evaluations/${r.id}`, { headers })).json(); completed = result; return result.status; }).toBe('completed');
    return completed!;
  }
  const oldRun = await run(oldA.id), newRun = await run(newA.id), bRun = await run(verB.id);
  const anomalies = await (await request.get(`/api/v1/quality/issues?project_id=${project.id}`, { headers })).json();
  const anomaly = anomalies.items[0];
  expect(anomaly.kind).toBe('anomaly');
  await post(`quality/issues/${anomaly.id}/update`, { expected_revision: 0, status: 'confirmed', note: '已核验旧版本失败' });
  await post(`quality/baselines/${project.id}`, { agent_id:agentA.id,benchmark_version_id:bv.id,grading_batch_id:oldRun.grading_batches[0].id,expected_revision:0 });
  const cA = await post('regressions/compare', { baseline_batch_id: oldRun.grading_batches[0].id, candidate_batch_id: newRun.grading_batches[0].id });
  await post('release-decisions', { comparison_id: cA.id, note: 'A 已修复版本' });
  const cB = await post('regressions/compare', { baseline_batch_id: bRun.grading_batches[0].id, candidate_batch_id: bRun.grading_batches[0].id });
  await post('release-decisions', { comparison_id: cB.id, note: 'B 独立决策' });
  await login(page);
  await page.getByLabel('当前项目', { exact: true }).selectOption(project.id);
  await page.getByLabel('Agent 评测身份').selectOption(agentA.id);
  await expect(page.locator('.metric-card').filter({ hasText: '评测通过率' }).locator('strong')).toHaveText('100%');
  await expect(page.locator('.metric-card').filter({ hasText: '正式待处理缺陷' }).locator('strong')).toHaveText('0');
  await expect(page.getByRole('heading', { name: '最近一次已保存的发布评估' })).toBeVisible();
  await page.getByLabel('候选版本', { exact: true }).selectOption(oldA.id);
  await expect(page.locator('.metric-card').filter({ hasText: '评测通过率' }).locator('strong')).toHaveText('0%');
  await expect(page.locator('.metric-card').filter({ hasText: '正式待处理缺陷' }).locator('strong')).toHaveText('1');
  await page.getByRole('link', { name: 'Bad Case 管理', exact: true }).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await page.locator('tbody tr').getByRole('link', { name: '分析与处理' }).click();
  await page.getByText('适用版本与范围（高级）', { exact: true }).click();
  await expect(page.getByLabel('适用版本 old')).toBeChecked();
  await expect(page.getByLabel('适用版本 fixed')).not.toBeChecked();
  await expect(page.getByLabel('适用版本 independent')).toHaveCount(0);
  await page.getByRole('link', { name: '质量工作台', exact: true }).click();
  await page.getByLabel('Agent 评测身份').selectOption(agentB.id);
  await expect(page.locator('.metric-card').filter({ hasText: '正式待处理缺陷' }).locator('strong')).toHaveText('0');
  await expect(page.getByRole('heading', { name: '最近一次已保存的发布评估' })).toHaveCount(0);
  await page.getByLabel('基线版本', { exact: true }).selectOption(verB.id);
  await expect(page.getByRole('heading', { name: '最近一次已保存的发布评估' })).toBeVisible();
  await page.getByRole('link', { name: '发布决策', exact: true }).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await expect(page.locator('tbody')).toContainText('Agent B');
  await expect(page.locator('tbody')).not.toContainText('Agent A');
  await page.getByRole('link', { name: 'Bad Case 管理', exact: true }).click();
  await expect(page.getByText('当前范围暂无匹配记录')).toBeVisible();
  await page.getByRole('link', { name: '项目总览', exact: true }).click();
  await expect(page.locator('.metric-card').filter({ hasText: '正式待处理缺陷' }).locator('strong')).toHaveText('1');
  await page.getByRole('link', { name: '质量工作台', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect.poll(() => page.locator('aside.sidebar').evaluate(e => e.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: '../.build/scope-mobile.png', fullPage: true });
});

test('team task query searches all pages, preserves URL context, rejects stale responses and fits target viewports', async ({ page, request }) => {
  const headers = { Authorization: `Bearer ${token}` };
  async function post(path: string, data: unknown) { const r = await request.post(`/api/v1/${path}`, { headers, data }); expect(r.ok(), await r.text()).toBeTruthy(); return r.json(); }
  const project = await post('projects', { name: '团队任务查询验收' });
  const agent = await post('agents', { project_id: project.id, name: '跨页订单助手', adapter_type: 'python' });
  const good = await post(`agents/${agent.id}/versions`, { version: '1', configuration: { plugin_id: 'example.order.v1' } });
  const bad = await post(`agents/${agent.id}/versions`, { version: 'bad', configuration: { plugin_id: 'example.order.v2' } });
  const bm = await post('benchmarks', { project_id: project.id, name: '共享订单测试集' });
  const bv = await post(`benchmarks/${bm.id}/versions`, { version: '1', cases: [{ case_key: 'one', input: { order_id: '1001' }, assertions: { expected_output: { order_id: '1001', status: 'shipped' } } }] });
  const profile = await post('profile-versions', { project_id: project.id, name: '输出规则', version: '1', evaluators: [{ instance_id: 'answer', id: 'exact_match' }] });
  const target = await post('evaluations', { agent_version_id: bad.id, benchmark_version_id: bv.id, profile_version_id: profile.id });
  let lastGood;
  for (let i = 0; i < 12; i++) lastGood = await post('evaluations', { agent_version_id: good.id, benchmark_version_id: bv.id, profile_version_id: profile.id });
  await login(page);
  await page.getByLabel('当前项目', { exact: true }).selectOption(project.id);
  await page.getByRole('link', { name: '评测任务', exact: true }).click();
  await expect(page.locator('.task-table tbody tr')).toHaveCount(10);
  await expect(page.locator('.task-table')).not.toContainText(target.id.slice(0,8));
  await page.getByLabel('搜索任务').fill(target.id);
  await expect(page.locator('.task-table tbody tr')).toHaveCount(1);
  const row = page.locator('.task-table tbody tr');
  await expect(row).toContainText('已完成'); await expect(row).toContainText('不通过');
  expect(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('q')).toBe(target.id);
  await row.getByRole('link', { name: '1 失败 →' }).click();
  await expect(page.getByLabel('用例结果筛选')).toHaveValue('bad');
  await expect(page.locator('.metric-card').filter({ hasText: '调用 P95' }).locator('strong')).toHaveText('样本不足');
  await expect(page.locator('.metric-card').filter({ hasText: '调用 P95' })).toContainText('最低 20 个');
  // An isolated UI contract fixture: empty reasons must not turn an unknown verdict into a pass claim.
  const reportPath = `**/api/v1/evaluations/${target.id}/report?**`;
  await page.route(reportPath, async route => {
    const response = await route.fetch(), data = await response.json();
    data.counts = { passed: 0, failed: 0, unknown: data.planned_trials, excluded: 0 };
    data.success_rate = null; data.decision_coverage = 0;
    data.trials = data.trials.map((t: object) => ({ ...t, verdict: 'unknown', reasons: [] }));
    data.quality_summary = { ...data.quality_summary, quality_status: 'insufficient', counts: data.counts };
    await route.fulfill({ response, json: data });
  });
  await page.getByRole('button', { name: '刷新任务', exact: true }).click();
  await expect(page.getByText('证据未形成有效判定', { exact: true })).toBeVisible();
  await expect(page.getByText('全部必需项通过', { exact: true })).toHaveCount(0);
  await expect(page.locator('.metric-card').filter({ hasText: '任务成功率' }).locator('strong')).toHaveText('—');
  await page.unroute(reportPath);
  await page.getByRole('link', { name: '全部评测任务', exact: true }).click();
  await expect(page.getByLabel('搜索任务')).toHaveValue(target.id);
  await expect(page.locator('.task-table tbody tr')).toHaveCount(1);
  let started!: () => void, finished!: () => void;
  const delayed = new Promise<void>(resolve => { started = resolve; }), fulfilled = new Promise<void>(resolve => { finished = resolve; });
  let failOnce = true;
  await page.route('**/api/v1/evaluations?**', async route => {
    if (new URL(route.request().url()).searchParams.get('q') === target.id.slice(0,20)) {
      const response = await route.fetch(); started(); await new Promise(r => setTimeout(r, 600)); await route.fulfill({ response }); finished();
    } else if (new URL(route.request().url()).searchParams.get('q') === 'failure-retry' && failOnce) {
      failOnce = false; await route.fulfill({ status: 503, json: { detail: { message: '临时查询不可用' } } });
    } else await route.continue();
  });
  await page.getByLabel('搜索任务').fill(target.id.slice(0,20)); await delayed;
  await page.getByLabel('搜索任务').fill('no-such-task');
  await fulfilled; await expect(page.getByRole('heading', { name: '没有匹配的任务', exact: true })).toBeVisible();
  await expect(page.locator('.task-table')).toHaveCount(0);
  await page.getByLabel('搜索任务').fill('failure-retry');
  await expect(page.getByRole('alert')).toContainText('临时查询不可用');
  await expect(page.getByLabel('搜索任务')).toHaveValue('failure-retry');
  await page.getByRole('button', { name: '重试查询', exact: true }).click();
  await expect(page.getByRole('heading', { name: '没有匹配的任务', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.unroute('**/api/v1/evaluations?**');
  await page.getByRole('button', { name: '清除筛选', exact: true }).click();
  await expect(page.locator('.task-table tbody tr')).toHaveCount(10);
  await page.getByLabel('筛选质量结论').selectOption('failed');
  await expect(page.locator('.task-table tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: '清除筛选', exact: true }).focus();
  await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab');
  expect(await page.getByRole('button', { name: '清除筛选', exact: true }).evaluate(e => getComputedStyle(e).outlineStyle)).not.toBe('none');
  const contrast = await page.locator('.page-heading p, .task-table .secondary-line, .task-table th, .form-note').evaluateAll(elements => {
    const lum = (rgb: number[]) => rgb.slice(0,3).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((n,v,i) => n + v * [.2126,.7152,.0722][i],0);
    return elements.map(e => {
      const style = getComputedStyle(e), fg = style.color.match(/[\d.]+/g)!.map(Number); let parent: Element | null = e; let bg = [12,17,27];
      while (parent) { const c = getComputedStyle(parent).backgroundColor.match(/[\d.]+/g)!.map(Number); if (c.length === 3 || c[3] === 1) { bg = c; break; } parent = parent.parentElement; }
      return { text: e.textContent?.slice(0,40), color: style.color, background: bg, size: style.fontSize, ratio: (Math.max(lum(fg),lum(bg))+.05)/(Math.min(lum(fg),lum(bg))+.05) };
    });
  });
  expect(contrast.every(c => c.ratio >= 4.5 && parseFloat(c.size) >= 12)).toBeTruthy();
  const { writeFile } = await import('node:fs/promises'); await writeFile('../.build/team-a-contrast.json', JSON.stringify(contrast,null,2));
  const source = await (await request.get(`/api/v1/evaluations/${target.id}`, { headers })).json();
  const fixed = await (await request.get(`/api/v1/evaluations/${lastGood.id}`, { headers })).json();
  const comparisonBody = { baseline_batch_id: source.grading_batches[0].id, candidate_batch_id: fixed.grading_batches[0].id };
  const c1 = await post('regressions/compare', comparisonBody), c2 = await post('regressions/compare', comparisonBody);
  await page.getByRole('link', { name: '发布决策', exact: true }).click();
  await page.getByLabel('候选版本', { exact: true }).selectOption(good.id);
  await page.getByLabel('基线版本', { exact: true }).selectOption(bad.id);
  await expect(page.getByLabel('选择固定版本比较').locator('option')).toHaveCount(3);
  const option1 = await page.getByLabel('选择固定版本比较').locator(`option[value="${c1.id}"]`).textContent();
  const option2 = await page.getByLabel('选择固定版本比较').locator(`option[value="${c2.id}"]`).textContent();
  expect(option1).not.toEqual(option2); expect(option1).toContain('跨页订单助手'); expect(option1).toContain('批次');
  await page.goto(`/#/runs?project_id=${project.id}&quality=failed`);
  await page.reload();
  // Reload returns to the explicit project after login; the shared deployment token stays in memory only.
  await page.getByLabel('访问令牌', { exact: true }).fill(token);
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByLabel('筛选质量结论')).toHaveValue('failed');
  await expect(page.locator('.task-table tbody tr')).toHaveCount(1);
  for (const [width,height,label] of [[1366,768,'desktop'],[1920,1080,'wide'],[390,844,'mobile']] as const) {
    await page.setViewportSize({ width,height });
    if (width === 390) await expect.poll(() => page.locator('aside.sidebar').evaluate(e => e.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: `../.build/team-a-${label}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: '../.build/team-a-zoom-200.png', fullPage: true });
});

test('personal sessions restore, members own issues, viewers cannot write, and revocation clears the workspace', async ({ page, request }) => {
  const adminHeaders = { Authorization: `Bearer ${token}` };
  async function post(path: string, data?: unknown) { const r = await request.post(`/api/v1/${path}`, { headers: adminHeaders, data }); expect(r.ok(), await r.text()).toBeTruthy(); return r.json(); }
  const password = 'browser-user-password-fixture-only';
  const project = await post('projects', { name: '团队身份浏览器验收' }), hidden = await post('projects', { name: '不可访问项目' });
  const developer = await post('team/users', { username: 'browserdev', display_name: '开发成员', password });
  const viewer = await post('team/users', { username: 'browserviewer', display_name: '只读成员', password });
  await post(`team/members/${project.id}`, { user_id: developer.id, role: 'evaluator' });
  await post(`team/members/${project.id}`, { user_id: viewer.id, role: 'viewer' });
  const agent = await post('agents', { project_id: project.id, name: '成员订单 Agent', adapter_type: 'python' });
  const av = await post(`agents/${agent.id}/versions`, { version: 'bad', configuration: { plugin_id: 'example.order.v2' } });
  const bm = await post('benchmarks', { project_id: project.id, name: '成员案例' });
  const bv = await post(`benchmarks/${bm.id}/versions`, { version: '1', cases: [{ case_key: 'one', input: { order_id: '1001' }, assertions: { expected_output: { order_id: '1001', status: 'shipped' } } }] });
  const profile = await post('profile-versions', { project_id: project.id, name: 'Rules', version: '1', evaluators: [{ instance_id: 'answer', id: 'exact_match' }] });
  await post('evaluations', { agent_version_id: av.id, benchmark_version_id: bv.id, profile_version_id: profile.id });
  async function personal(username: string) {
    await page.getByRole('button', { name: '使用个人账号登录', exact: true }).click();
    await page.getByLabel('用户名', { exact: true }).fill(username); await page.getByLabel('密码', { exact: true }).fill(password);
    await page.getByRole('button', { name: '登录团队工作台', exact: true }).click();
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  }
  await page.goto('/'); await personal('browserdev');
  await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(project.id);
  await expect(page.getByLabel('当前项目').locator('option')).toHaveCount(1);
  await page.reload();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await expect(page.getByLabel('访问令牌')).toHaveCount(0);
  const cookies = await page.context().cookies(); const session = cookies.find(c => c.name === 'agenteval_session')!;
  expect(session.httpOnly).toBe(true); expect(session.sameSite).toBe('Strict');
  expect(await page.evaluate(() => document.cookie)).not.toContain(session.value);
  await page.getByRole('link', { name: 'Bad Case 管理', exact: true }).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  const bounds = await page.locator('tbody tr').first().boundingBox(); expect(bounds!.y).toBeLessThan(768);
  await page.locator('tbody tr').getByRole('link', { name: '分析与处理' }).click();
  await page.getByLabel('负责人', { exact: true }).selectOption(developer.id);
  await page.getByLabel('处理状态').selectOption('confirmed');
  await page.getByLabel('本次处理说明').fill('成员确认并负责修复');
  await page.getByRole('button', { name: '保存处理记录', exact: true }).click();
  await expect(page.locator('.page-heading p')).toContainText('已确认');
  await expect(page.getByRole('heading', { name: '处理时间线' }).locator('..').locator('..')).toContainText('开发成员');
  await page.getByRole('link', { name: 'Bad Case 管理', exact: true }).click();
  await page.getByRole('checkbox', { name: '分配给我', exact: true }).check();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await page.screenshot({ path: '../.build/team-b-member.png', fullPage: true });
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page.getByLabel('访问令牌', { exact: true })).toBeVisible();
  await personal('browserviewer');
  await expect(page.getByText('当前项目为只读权限，可查看结果和导出报告。', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '评测任务', exact: true }).click();
  await expect(page.getByRole('button', { name: '创建评测', exact: true })).toBeDisabled();
  const me = await (await page.request.get('/api/v1/auth/session')).json();
  const csrfHeaders = { Origin: new URL(page.url()).origin, 'X-CSRF-Token': me.csrf_token };
  expect((await page.request.post('/api/v1/agents', { headers: csrfHeaders, data: { project_id: project.id, name: 'forbidden', adapter_type: 'python' } })).status()).toBe(403);
  expect((await page.request.get(`/api/v1/workspace/${hidden.id}`)).status()).toBe(404);
  expect((await page.request.get('/api/v1/team/users')).status()).toBe(403);
  await page.screenshot({ path: '../.build/team-b-viewer.png', fullPage: true });
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(stored).not.toContain(password); expect(stored).not.toContain(token); expect(stored).not.toContain(session.value);
  await post(`auth/sessions/${me.session_id}/revoke`);
  await page.reload();
  await expect(page.getByLabel('访问令牌', { exact: true })).toBeVisible();
  await expect(page.locator('.task-table')).toHaveCount(0);
});

test('HTTP credential reference UI performs only explicit authenticated test calls and normal evaluations', async ({ page, request }) => {
  const { readFile } = await import('node:fs/promises');
  const fixture = JSON.parse(await readFile('../.build/http-browser-fixture.json', 'utf-8'));
  const headers = { Authorization: `Bearer ${token}` };
  async function post(path: string, data?: unknown) { const r = await request.post(`/api/v1/${path}`, { headers, data }); expect(r.ok(), await r.text()).toBeTruthy(); return r.json(); }
  const project = await post('projects', { name: '真实网络鉴权夹具验收' });
  await login(page); await page.getByLabel('当前项目', { exact: true }).selectOption(project.id);
  await page.getByRole('link', { name: '团队与凭据', exact: true }).click();
  await page.getByLabel('凭据版本名称', { exact: true }).fill('Browser bearer');
  await page.getByLabel('服务端环境变量名', { exact: true }).fill('AGENTEVAL_AGENT_SECRET_BROWSER_HTTP');
  await page.getByRole('button', { name: '登记凭据版本', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已保存');
  const credentials = await (await request.get(`/api/v1/team/credentials?project_id=${project.id}`, { headers })).json();
  const agent = await post('agents', { project_id: project.id, name: 'Authenticated fixture', adapter_type: 'http' });
  const av = await post(`agents/${agent.id}/versions`, { version: '1', configuration: { endpoint: fixture.endpoint, credential_version_id: credentials.items[0].id } });
  const bm = await post('benchmarks', { project_id: project.id, name: 'Explicit network case' });
  const bv = await post(`benchmarks/${bm.id}/versions`, { version: '1', cases: [{ case_key: 'one', input: { query: 'fixture' }, assertions: { expected_output: 'ok' } }] });
  await page.getByRole('link', { name: 'HTTP 接入验证', exact: true }).click();
  await expect(page.getByLabel('明确选择测试用例')).toHaveValue(/.+/);
  const statsUrl = new URL('/stats', fixture.endpoint).toString();
  expect((await (await request.get(statsUrl)).json()).posts).toBe(0);
  await expect(page.getByRole('button', { name: '执行明确的测试调用', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '仅检查地址与网络（HEAD）', exact: true }).click();
  await expect(page.getByRole('heading', { name: '本次验证结果' })).toBeVisible();
  expect((await (await request.get(statsUrl)).json()).posts).toBe(0);
  await page.getByRole('checkbox', { name: /我确认这是受控测试环境/ }).check();
  await page.getByRole('button', { name: '执行明确的测试调用', exact: true }).click();
  await expect(page.getByText('协议已验证；未声明内部观测能力。', { exact: true })).toBeVisible();
  expect((await (await request.get(statsUrl)).json()).posts).toBe(1);
  const profile = await post('profile-versions', { project_id: project.id, name: 'Exact', version: '1', evaluators: [{ instance_id: 'answer', id: 'exact_match' }] });
  const run = await post('evaluations', { agent_version_id: av.id, benchmark_version_id: bv.id, profile_version_id: profile.id });
  await expect.poll(async () => { const r = await request.get(`/api/v1/evaluations/${run.id}/report`, { headers }); return (await r.json()).success_rate; }).toBe(1);
  const exported = await request.get(`/api/v1/evaluations/${run.id}/export?grading_batch_id=${run.grading_batch_id}`, { headers });
  expect(await exported.text()).not.toContain('browser-http-fixture-secret-not-real');
  expect((await (await request.get(`/api/v1/team/credentials?project_id=${project.id}`, { headers })).text())).not.toContain('fingerprint');
  await page.screenshot({ path: '../.build/team-b-http.png', fullPage: true });
});

test('protected views keep secrets hidden, policy UI versions settings, and original access is explicit and audited', async ({ page, request }) => {
  const headers = { Authorization: `Bearer ${token}` };
  async function post(path: string, data?: unknown) { const r = await request.post(`/api/v1/${path}`, { headers, data }); expect(r.ok(), await r.text()).toBeTruthy(); return r.json(); }
  const project = await post('projects', { name: '证据保护与隔离验收' });
  const agent = await post('agents', { project_id: project.id, name: 'Protected fixture', adapter_type: 'python' });
  const av = await post(`agents/${agent.id}/versions`, { version: '1', configuration: { plugin_id: 'example.order.v1' } });
  const bm = await post('benchmarks', { project_id: project.id, name: 'Protected inputs' });
  const secret = 'browser-sensitive-business-contact-only';
  const bv = await post(`benchmarks/${bm.id}/versions`, { version: '1', cases: [{ case_key: 'one', input: { order_id: '1001', business_note: secret, customer: { phone: '13800000000' } }, assertions: { expected_output: { order_id: '1001', status: 'shipped' } } }] });
  const profile = await post('profile-versions', { project_id: project.id, name: 'Rules', version: '1', evaluators: [{ instance_id: 'answer', id: 'exact_match' }] });
  const run = await post('evaluations', { agent_version_id: av.id, benchmark_version_id: bv.id, profile_version_id: profile.id });
  await login(page); await page.getByLabel('当前项目', { exact: true }).selectOption(project.id);
  await page.getByRole('link', { name: '团队与凭据', exact: true }).click();
  await page.getByLabel('字段路径（每行一个；支持 * 单层、** 多层）').fill('/**/customer/phone');
  await page.getByLabel('内容秘密的服务端变量名（每行一个，不填秘密值）').fill('AGENTEVAL_REDACTION_SECRET_BROWSER');
  await page.getByRole('button', { name: '发布新的保护策略', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已发布策略修订 1');
  const ordinary = await request.get(`/api/v1/benchmark-versions/${bv.id}/cases`, { headers });
  expect(await ordinary.text()).not.toContain(secret); expect(await ordinary.text()).not.toContain('13800000000');
  const denied = await request.post(`/api/v1/protection/evaluations/${run.id}/raw`, { headers, data: { reason: 'Shared identity must not see originals' } });
  expect(denied.status()).toBe(403);
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await page.getByRole('button', { name: '使用个人账号登录', exact: true }).click();
  await page.getByLabel('用户名', { exact: true }).fill('fixtureadmin');
  await page.getByLabel('密码', { exact: true }).fill('browser-admin-password-fixture-only');
  await page.getByRole('button', { name: '登录团队工作台', exact: true }).click();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeAttached();
  await page.getByLabel('当前项目', { exact: true }).selectOption(project.id);
  await page.goto(`/#/runs/${run.id}?project_id=${project.id}`);
  const panel = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: '受审计的原始证据', exact: true }) });
  await expect(panel).toBeVisible();
  await expect(page.locator('body')).not.toContainText(secret);
  await panel.getByText('按授权读取原文（需理由）', { exact: true }).click();
  await panel.getByLabel('原文读取理由（至少 8 个字符）').fill('受控验收检查原始业务输入');
  await panel.getByRole('button', { name: '读取原始证据并记录审计', exact: true }).click();
  await expect(panel.locator('pre')).toContainText(secret);
  await expect(panel.getByText(/原文读取审计/)).toBeVisible();
  await panel.getByRole('button', { name: '关闭并清除原文', exact: true }).click();
  await expect(panel.locator('pre')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(secret);
  await page.screenshot({ path: '../.build/protection-default-view.png', fullPage: true });
});


test('copy evaluation, private draft restoration and lost response recovery create exactly one new run',async ({page,request}) => {
  const headers={Authorization:`Bearer ${token}`};
  async function post(path:string,data:unknown) { const r=await request.post(`/api/v1/${path}`,{headers,data}); expect(r.ok(),await r.text()).toBeTruthy(); return r.json(); }
  const f=await fixture(post,'复用与个人草稿验收');
  await expect.poll(async () => (await (await request.get(`/api/v1/evaluations/${f.run.id}`,{headers})).json()).status).toBe('completed');
  await page.goto('/'); await page.getByRole('button',{name:'使用个人账号登录',exact:true}).click();
  await page.getByLabel('用户名',{exact:true}).fill('fixtureadmin'); await page.getByLabel('密码',{exact:true}).fill('browser-admin-password-fixture-only');
  await page.getByRole('button',{name:'登录团队工作台',exact:true}).click();
  await page.getByLabel('当前项目',{exact:true}).selectOption(f.project.id);
  await page.goto(`/#/runs/${f.run.id}?project_id=${f.project.id}`);
  await page.getByRole('link',{name:'复制配置评测',exact:true}).click();
  await expect(page.getByLabel('复用 Agent 版本')).toHaveValue(f.av.id);
  await expect(page.getByLabel('复用 Benchmark 版本')).toHaveValue(f.bv.id);
  await expect(page.getByLabel('复用评分方案')).toHaveValue(f.profile.id);
  await page.getByRole('button',{name:'保存我的草稿',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('草稿已保存');
  await page.getByLabel('复用 Agent 版本').selectOption(f.av2.id);
  page.once('dialog',dialog => dialog.dismiss());
  await page.getByRole('link',{name:'Agent 管理',exact:true}).click();
  await expect(page.getByLabel('复用 Agent 版本')).toHaveValue(f.av2.id);
  await page.getByRole('button',{name:'恢复我的草稿',exact:true}).click();
  await expect(page.getByLabel('复用 Agent 版本')).toHaveValue(f.av.id);
  await page.getByRole('button',{name:'预检并核对配置',exact:true}).click();
  await expect(page.getByRole('heading',{name:'提交前配置确认'})).toBeVisible();
  let committed='';
  await page.route('**/api/v1/onboarding/reuse',async route => { const r=await route.fetch(); expect(r.ok(),await r.text()).toBeTruthy(); committed=(await r.json()).id; await route.abort('failed'); });
  await page.getByRole('button',{name:'执行复用评测',exact:true}).click();
  await expect(page.getByRole('heading',{name:'提交结果尚未确认'})).toBeVisible();
  await page.reload();
  await page.getByRole('button',{name:'恢复我的草稿',exact:true}).click();
  await expect(page.getByRole('heading',{name:'提交结果尚未确认'})).toBeVisible();
  await page.getByRole('button',{name:'查询原请求结果',exact:true}).click();
  await expect(page.getByRole('heading',{name:'用例执行结果'})).toBeVisible();
  expect(new URL(page.url()).hash.split('?')[0]).toBe(`/runs/${committed}`.replace('/runs/','#/runs/'));
  const list=await (await request.get(`/api/v1/evaluations?project_id=${f.project.id}`,{headers})).json();
  expect(list.total).toBe(2);
  const workspace=await (await request.get(`/api/v1/workspace/${f.project.id}`,{headers})).json();
  expect(workspace.totals.agents).toBe(1); expect(workspace.totals.benchmarks).toBe(1); expect(workspace.totals.profiles).toBe(1);
  await page.screenshot({path:'../.build/workflow-reuse-recovered.png',fullPage:true});
});

test('saved baseline and fixed batch survive late old runs, refresh, login, URL history and direction swap',async ({page,request}) => {
  const headers={Authorization:`Bearer ${token}`};
  async function post(path:string,data:unknown) { const r=await request.post(`/api/v1/${path}`,{headers,data}); expect(r.ok(),await r.text()).toBeTruthy(); return r.json(); }
  const f=await fixture(post,'显式基线与链接恢复验收');
  const candidate=await post('evaluations',{agent_version_id:f.av2.id,benchmark_version_id:f.bv.id,profile_version_id:f.profile.id});
  await expect.poll(async () => (await (await request.get(`/api/v1/evaluations/${candidate.id}`,{headers})).json()).status).toBe('completed');
  await login(page); await page.getByLabel('当前项目',{exact:true}).selectOption(f.project.id);
  await page.getByLabel('候选版本',{exact:true}).selectOption(f.av2.id);
  await page.getByLabel('基线版本',{exact:true}).selectOption(f.av.id);
  await page.getByText('固定评分批次与显式基线',{exact:true}).click();
  await expect(page.getByLabel('基线评分批次',{exact:true})).toHaveValue(f.run.grading_batch_id);
  await page.getByRole('button',{name:'保存为此范围的显式基线',exact:true}).click();
  await expect.poll(async () => (await (await request.get(`/api/v1/quality/baselines/${f.project.id}`,{headers})).json()).items.length).toBe(1);
  const late=await post('evaluations',{agent_version_id:f.av.id,benchmark_version_id:f.bv.id,profile_version_id:f.profile.id});
  await expect.poll(async () => (await (await request.get(`/api/v1/evaluations/${late.id}`,{headers})).json()).status).toBe('completed');
  await page.getByRole('button',{name:'刷新范围',exact:true}).click();
  await expect(page.getByLabel('基线评分批次',{exact:true})).toHaveValue(f.run.grading_batch_id);
  await expect(page.getByLabel('候选评分批次',{exact:true})).toHaveValue(candidate.grading_batch_id);
  const shared=page.url(); expect(shared).toContain('baseline_batch_id=');
  await page.reload(); await page.getByLabel('访问令牌',{exact:true}).fill(token); await page.getByRole('button',{name:'进入工作台',exact:true}).click();
  await expect(page.getByLabel('候选版本',{exact:true})).toHaveValue(f.av2.id);
  await page.getByText('固定评分批次与显式基线',{exact:true}).click();
  await page.getByRole('button',{name:'交换基线与候选',exact:true}).click();
  await expect(page.getByLabel('候选版本',{exact:true})).toHaveValue(f.av.id);
  await page.goBack(); await expect(page.getByLabel('候选版本',{exact:true})).toHaveValue(f.av2.id);
  await expect(page.getByLabel('基线评分批次',{exact:true})).toHaveValue(f.run.grading_batch_id);
  const invalid=new URL(shared); invalid.hash=invalid.hash.replace(candidate.grading_batch_id,'unavailable-batch');
  await page.goto(invalid.toString());
  await expect(page.getByRole('alert')).toContainText('范围或批次已失效');
  await page.getByLabel('Agent 评测身份',{exact:true}).selectOption(f.agent.id);
  await page.getByLabel('候选版本',{exact:true}).selectOption(f.av2.id);
  await expect(page.locator('.metric-card').filter({hasText:'评测通过率'}).locator('strong')).toHaveText('0%');
  await page.setViewportSize({width:390,height:844});
  await expect.poll(() => page.locator('aside.sidebar').evaluate(e => e.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({path:'../.build/workflow-scope-mobile.png',fullPage:true});
});

test('case table editing preserves nested JSON, rejects invalid imports atomically and displays version differences',async ({page,request}) => {
  const headers={Authorization:`Bearer ${token}`};
  async function post(path:string,data:unknown) { const r=await request.post(`/api/v1/${path}`,{headers,data}); expect(r.ok(),await r.text()).toBeTruthy(); return r.json(); }
  const project=await post('projects',{name:'用例编辑与版本差异验收'}), bm=await post('benchmarks',{project_id:project.id,name:'Complex data'});
  const a={case_key:'A',input:{nested:{items:[1,{preserved:true}]}},assertions:{expected_output:'old'},tags:['golden']};
  const b={...a,case_key:'B'};
  await login(page); await page.getByLabel('当前项目',{exact:true}).selectOption(project.id); await page.getByRole('link',{name:'Benchmark',exact:true}).click();
  await page.getByRole('button',{name:'导入新版本',exact:true}).click(); const modal=page.getByRole('dialog');
  await modal.getByLabel('版本号',{exact:true}).fill('1'); await modal.getByLabel('用例内容').fill(JSON.stringify([a,b]));
  await modal.getByText(/表格预览与基础编辑/).click();
  await modal.getByRole('button',{name:'编辑用例 1',exact:true}).click();
  const raw = modal.getByLabel('用例内容');
  const broken = JSON.stringify([a,b]).slice(0,-1);
  await raw.fill(broken);
  await expect(modal.getByRole('button',{name:'添加用例',exact:true})).toBeDisabled();
  await expect(modal.getByRole('button',{name:'删除用例 1',exact:true})).toHaveCount(0);
  await expect(modal.getByRole('button',{name:'应用单条修改',exact:true})).toHaveCount(0);
  await expect(raw).toHaveValue(broken);
  await raw.fill(JSON.stringify([a,b]));
  await modal.getByRole('button',{name:'编辑用例 1',exact:true}).click();
  await modal.locator('input[type=file]').setInputFiles({ name: 'replacement.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify([b,a])) });
  await expect(raw).toHaveValue(JSON.stringify([b,a]));
  await expect(modal.getByRole('button',{name:'应用单条修改',exact:true})).toHaveCount(0);
  await raw.fill('');
  await modal.getByRole('button',{name:'添加用例',exact:true}).click();
  await expect(raw).toHaveValue(/case-1/);
  await raw.fill(JSON.stringify([{...a,case_key:'case-2'}]));
  await modal.getByRole('button',{name:'添加用例',exact:true}).click();
  expect(JSON.parse(await raw.inputValue()).map((item: { case_key: string }) => item.case_key)).toEqual(['case-2','case-3']);
  await raw.fill(JSON.stringify([a,b]));
  await modal.getByRole('button',{name:'编辑用例 1',exact:true}).click();
  await modal.getByLabel('单条用例完整 JSON（保留嵌套结构）').fill(JSON.stringify({...a,assertions:{expected_output:'edited'}}));
  await modal.getByRole('button',{name:'应用单条修改',exact:true}).click();
  await modal.getByRole('button',{name:'添加用例',exact:true}).click(); await modal.getByRole('button',{name:'按行与字段预检',exact:true}).click();
  await expect(modal.getByRole('alert')).toContainText('/assertions');
  await modal.getByRole('button',{name:'删除用例 3',exact:true}).click(); await modal.getByRole('button',{name:'校验并发布版本',exact:true}).click();
  await expect(modal).toHaveCount(0);
  const v1=(await (await request.get(`/api/v1/benchmarks/${bm.id}/versions`,{headers})).json()).items[0];
  const original=(await (await request.get(`/api/v1/benchmark-versions/${v1.id}/cases`,{headers})).json()).items.find((c:{definition:{case_key:string}}) => c.definition.case_key === 'A').definition;
  expect(original.input).toEqual(a.input); expect(original.assertions.expected_output).toBe('edited');
  const v2=await post(`benchmarks/${bm.id}/publish`,{version:'2',document:JSON.stringify([{...a,assertions:{expected_output:'new'}},{...b,case_key:'C'}])});
  await page.getByRole('button',{name:'刷新数据',exact:true}).click();
  await page.getByText('查看固定版本新增 / 删除 / 修改差异',{exact:true}).click();
  await page.getByLabel('数据集基线版本').selectOption(v1.id); await page.getByLabel('数据集候选版本').selectOption(v2.id);
  await page.getByRole('button',{name:'比较数据集版本',exact:true}).click();
  await expect(page.getByText('新增 1 条 · 删除 1 条 · 修改 1 条 · 未变更 0 条')).toBeVisible();
  await page.getByText('修改用例 A', { exact: true }).click();
  await expect(page.getByLabel('数据集版本差异').locator('pre').first()).toContainText('edited');
  await expect(page.getByLabel('数据集版本差异').locator('pre').last()).toContainText('new');
  await page.screenshot({path:'../.build/workflow-case-diff.png',fullPage:true});
});

test('historical detail and tracking pin the selected run and batch across navigation', async ({ page, request }) => {
  const headers = { Authorization: `Bearer ${token}` };
  async function post(path: string, data?: unknown) {
    const r = await request.post(`/api/v1/${path}`, { headers, data });
    expect(r.ok(), await r.text()).toBeTruthy();
    return r.json();
  }
  async function report(run: string, batch: string) {
    const url = `/api/v1/evaluations/${run}/report?grading_batch_id=${batch}`;
    return (await request.get(url, { headers })).json();
  }
  const f = await fixture(post, '历史固定证据复查');
  const failing = await post('profile-versions', { project_id: f.project.id, name: 'Reject shipped', version: '1', evaluators: [{
    instance_id: 'schema', id: 'json_schema', config: { schema: { type: 'object', required: ['status'], properties: { status: { const: 'cancelled' } } } },
  }] });
  const inputs = { agent_version_id: f.av.id, benchmark_version_id: f.bv.id };
  const old = await post('evaluations', { ...inputs, profile_version_id: failing.id });
  const newer = await post('evaluations', { ...inputs, profile_version_id: f.profile.id });
  await expect.poll(async () => (await report(old.id, old.grading_batch_id)).success_rate).toBe(0);
  await expect.poll(async () => (await report(newer.id, newer.grading_batch_id)).success_rate).toBe(1);
  const oldSecond = await post(`evaluations/${old.id}/regrade`);
  const newSecond = await post(`evaluations/${newer.id}/regrade`);
  await expect.poll(async () => (await report(old.id, oldSecond.id)).grading_status).toBe('completed');
  await expect.poll(async () => (await report(newer.id, newSecond.id)).grading_status).toBe('completed');
  const scope = () => new URLSearchParams(new URL(page.url()).hash.split('?')[1]);
  await page.goto(`/#/runs/${old.id}?project_id=${f.project.id}`);
  await page.getByLabel('访问令牌', { exact: true }).fill(token);
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByLabel('评分批次', { exact: true })).toHaveValue(oldSecond.id);
  await expect.poll(() => scope().get('candidate_run_id')).toBe(old.id);
  await expect.poll(() => scope().get('candidate_batch_id')).toBe(oldSecond.id);
  await page.getByLabel('评分批次', { exact: true }).selectOption(old.grading_batch_id);
  await expect.poll(() => scope().get('grading_batch_id')).toBe(old.grading_batch_id);
  await page.getByRole('button', { name: '跟踪本批次异常' }).click();
  await expect(page.getByRole('heading', { name: 'Bad Case 管理' })).toBeVisible();
  await expect.poll(() => scope().get('candidate_batch_id')).toBe(old.grading_batch_id);
  expect(scope().get('candidate_run_id')).toBe(old.id);
  const found = await (await request.get(`/api/v1/quality/issues?${scope()}`, { headers })).json();
  expect(found.items.map((item: { batch_id: string }) => item.batch_id)).toEqual([old.grading_batch_id]);
  await page.getByText('固定评分批次与显式基线', { exact: true }).click();
  await expect(page.getByLabel('候选运行', { exact: true })).toHaveValue(old.id);
  const batches = page.getByLabel('候选评分批次', { exact: true });
  await expect(batches.locator('option')).toHaveCount(3);
  await expect(batches.locator(`option[value="${newSecond.id}"]`)).toHaveCount(0);

  const historyCalls: string[] = [];
  const started = Date.now();
  page.on('request', r => { if (/\/quality\/(runs|batches|scope-selection|scope-options)(\/|\?|$)/.test(r.url())) historyCalls.push(r.url()); });
  await page.getByRole('link', { name: '质量工作台', exact: true }).click();
  const source = page.getByRole('link', { name: '查看来源评测', exact: true });
  await expect(source).toHaveAttribute('href', new RegExp(`${old.id}.*grading_batch_id=${old.grading_batch_id}`));
  await page.getByRole('link', { name: '发布决策', exact: true }).click();
  await expect(page.getByRole('heading', { name: '版本发布决策' })).toBeVisible();
  await expect.poll(() => scope().get('candidate_run_id')).toBe(old.id);
  expect(scope().get('candidate_batch_id')).toBe(old.grading_batch_id);
  expect(historyCalls).toEqual([]);
  const { writeFile } = await import('node:fs/promises');
  await writeFile('../.build/workflow-scope-navigation.json', JSON.stringify({ transitions: 2, history_requests: 0,
    history_response_bytes: 0, elapsed_ms: Date.now() - started }, null, 2));
});

test('a restored pending draft can query a missing receipt and retry the identical request', async ({ page, request }) => {
  const headers = { Authorization: `Bearer ${token}` };
  async function post(path: string, data: unknown) {
    const r = await request.post(`/api/v1/${path}`, { headers, data });
    expect(r.ok(), await r.text()).toBeTruthy();
    return r.json();
  }
  const f = await fixture(post, '恢复未提交请求复查');
  await page.goto('/');
  await page.getByRole('button', { name: '使用个人账号登录' }).click();
  await page.getByLabel('用户名').fill('fixtureadmin');
  await page.getByLabel('密码').fill('browser-admin-password-fixture-only');
  await page.getByRole('button', { name: '登录团队工作台' }).click();
  await page.getByLabel('当前项目').selectOption(f.project.id);
  await page.goto(`/#/start?project_id=${f.project.id}&source_run_id=${f.run.id}`);
  await expect(page.getByLabel('复用评分方案')).toHaveValue(f.profile.id);
  await page.getByRole('button', { name: '保存我的草稿' }).click();
  await expect(page.getByRole('status')).toContainText('草稿已保存');

  const draftUrl = `/api/v1/onboarding/drafts/${f.project.id}`;
  const saved = await (await page.request.get(draftUrl)).json();
  const session = await (await page.request.get('/api/v1/auth/session')).json();
  const remote = await page.request.post(draftUrl, { headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token },
    data: { expected_revision: saved.revision, payload: { ...saved.payload, agent_version_id: f.av2.id } } });
  expect(remote.ok(), await remote.text()).toBeTruthy();
  await page.getByRole('button', { name: '保存我的草稿' }).click();
  await page.getByRole('button', { name: '读取最新草稿，保留当前输入' }).click();
  await expect(page.getByLabel('复用 Agent 版本')).toHaveValue(f.av.id);
  await page.getByRole('button', { name: '保留当前配置' }).click();

  await page.getByRole('button', { name: '预检并核对配置' }).click();
  let original: unknown;
  await page.route('**/api/v1/onboarding/reuse', async route => {
    original = route.request().postDataJSON();
    await route.abort('failed');
  }, { times: 1 });
  await page.getByRole('button', { name: '执行复用评测' }).click();
  await expect(page.getByRole('heading', { name: '提交结果尚未确认' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '恢复我的草稿' }).click();
  await page.getByRole('button', { name: '查询原请求结果' }).click();
  await expect(page.getByRole('alert')).toContainText('尚无已提交结果');
  const retry = page.waitForRequest(r => r.url().endsWith('/api/v1/onboarding/reuse') && r.method() === 'POST');
  await page.getByRole('button', { name: '按原请求重试' }).click();
  expect((await retry).postDataJSON()).toEqual(original);
  await expect(page.getByRole('heading', { name: '用例执行结果' })).toBeVisible();
  const list = await (await request.get(`/api/v1/evaluations?project_id=${f.project.id}`, { headers })).json();
  expect(list.total).toBe(2);
});

test('first-project recovery can query and retry before a project ID exists', async ({ page }) => {
  let committed = '';
  await page.route('**/api/v1/projects?*', async route => {
    if (route.request().method() === 'GET' && !committed) await route.fulfill({ json: { items: [], total: 0 } });
    else await route.continue();
  });
  await login(page);
  await expect(page.getByRole('heading', { name: /从一个项目开始/ })).toBeVisible();
  await page.getByRole('link', { name: '三步开始评测' }).click();
  await page.getByLabel('项目名称', { exact: true }).fill('首次项目提交恢复');
  await expect(page.getByLabel('部署插件')).toHaveValue('');
  await page.getByLabel('部署插件').selectOption('example.order.v1');
  await page.getByRole('button', { name: '校验接入并继续' }).click();
  await page.getByRole('button', { name: '下一步：准备测试用例' }).click();
  await expect(page.getByLabel('测试用例', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: '填入订单演示用例' }).click();
  await page.getByRole('button', { name: '预检并核对配置' }).click();
  await page.route('**/api/v1/onboarding/evaluations', route => route.fulfill({ status: 503, json: { code: 'FIXTURE_UNCOMMITTED' } }), { times: 1 });
  await page.getByRole('button', { name: '执行评测并查看分析' }).click();
  await expect(page.getByRole('button', { name: '返回修改配置' })).toBeEnabled();
  await page.getByRole('button', { name: '查询原请求结果' }).click();
  await expect(page.getByRole('alert')).toContainText('尚无已提交结果');
  await page.route('**/api/v1/onboarding/evaluations', async route => {
    const result = await route.fetch();
    expect(result.ok(), await result.text()).toBeTruthy();
    committed = (await result.json()).id;
    await route.abort('failed');
  }, { times: 1 });
  await page.getByRole('button', { name: '按原请求重试' }).click();
  await expect(page.getByRole('button', { name: '查询原请求结果' })).toBeEnabled();
  await page.getByRole('button', { name: '查询原请求结果' }).click();
  await expect(page.getByRole('heading', { name: '用例执行结果' })).toBeVisible();
  expect(new URL(page.url()).hash.split('?')[0]).toBe(`#/runs/${committed}`);
});

test('local calendar filters preserve UTC boundaries on a daylight saving transition', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, timezoneId: 'America/New_York' });
  try {
    const page = await context.newPage();
    await login(page);
    await page.getByRole('link', { name: '评测任务', exact: true }).click();
    await page.getByLabel('起始日期（America/New_York）').fill('2026-03-08');
    await page.getByLabel('结束日期（America/New_York）').fill('2026-03-08');
    const query = new URLSearchParams(new URL(page.url()).hash.split('?')[1]);
    expect(query.get('created_after')).toBe('2026-03-08T05:00:00.000Z');
    expect(query.get('created_before')).toBe('2026-03-09T03:59:59.999999Z');
    await expect(page.getByLabel('结束日期（America/New_York）')).toHaveValue('2026-03-08');
  } finally { await context.close(); }
});
