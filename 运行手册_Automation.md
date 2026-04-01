# 运行手册（Automation）

## 1. 环境变量
- `AUTOMATION_USE_MOCK=1`：默认，使用 mock 浏览器流程。
- `AUTOMATION_USE_MOCK=0`：启用真实 Playwright 页面自动化。
- `AUTOMATION_TARGET_URL`：真实模式下目标平台登录页。
- `AUTOMATION_WORKFLOW
_API_BASE`：自动化服务回调旧工作流 API 地址，默认 `http://127.0.0.1:8080`。
- `AUTOMATION_SKIP_BROWSER_INSTALL=1`：启动脚本跳过 `playwright install chromium`。

## 2. 启动
- Windows: 运行 `start.bat`
- Linux/macOS: 运行 `./start.sh`

## 3. 控制台入口
- `http://localhost:5173/automation-console.html`

## 4. 标准操作流程
1. 启动会话（输入账号、密码、运行模式）
2. 点击扫描

3. 勾选候选任务并确认勾选
4. 点击接单
5. 点击解题
6. 在 `review_pending` 任务中编辑并保存
7. 点击确认提交

## 5. 人工接管
- headed 模式下可直接观察浏览器操作。
- 必要时点击 pause 暂停，人工处理后 resume。

## 6. 失败处置
- `solve_failed`：不自动提交，后续人工重新入队处理。
- `failed_submit`：等待人工检查页面后重试提交。
- `skipped`：超时自动跳过，后续人工筛选处理。

## 7. 中断语义
- `stop` 是硬中断：
  - 取消当前自动化后台任务
  - 停止后续点击/提交/工作流调用
  - 关闭 run 对应浏览器会话
