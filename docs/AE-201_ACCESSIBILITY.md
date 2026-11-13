# AE-201 键盘操作与无障碍回归

2026-11-13。工作台加入「跳到主要内容」链接。切换页面后焦点进入主区。窄屏导航打开后从当前页面对应的链接开始，Tab 在导航内循环，Escape 关闭并返回打开按钮。选择新页面后焦点进入主区。

证据弹窗以可见标题命名，Escape 关闭后焦点返回「查看证据」。Trace 按钮公开选中状态，执行链路加载时显示独立提示。登录、侧栏、空态、页脚和 Trace 辅助文字使用统一的次要文字颜色。

Playwright 使用 axe 的 WCAG 2.0/2.1 A 与 AA 规则，覆盖令牌登录、个人登录、质量工作台、评测向导、任务列表、Bad Case、发布决策、团队与凭据、运行详情、证据弹窗及移动导航。回归也以键盘操作验证焦点顺序与恢复行为。CI 工件 `accessibility-checks.json` 记录每个状态通过的规则数与需人工核查的规则。后续人工验收包含真实读屏操作与不同辅助技术的交互测试。 [Playwright 无障碍测试指南](https://playwright.dev/docs/accessibility-testing)

```powershell
cd frontend
npm ci
npm run build
npm run test:e2e -- --grep 'core workflows pass accessibility'
```
