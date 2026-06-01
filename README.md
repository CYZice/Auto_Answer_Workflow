# 智能题目解析 Agent 自动化流水线 (Zyb-Agent)

这是一个基于大语言模型（如 GPT-4o-mini）和 LangGraph 构建的自动化题目解析流水线系统。它支持多模态图片的批量上传、自动识图解题、结构化机器审查、自动排版，并具备完备的人工接管与异常重试机制。

## 系统架构与特性

- **后端**: FastAPI + LangGraph + SQLite + SQLAlchemy
  - **状态机**: 严格控制任务在 `queued -> solving -> reviewing -> formatting` 之间的流转。
  - **断点恢复**: 基于 SQLite (WAL 模式) 的持久化机制，支持任务挂起与人工回流。
  - **并发控制**: 后端强制限制并发任务数，避免触发 LLM API 的 Rate Limit。
  - **熔断机制**: 支持在流转过程中实时响应中断/取消指令。
- **前端**: Vite + React + Tailwind CSS + TanStack Query
  - **极速交互**: 支持直接 `Ctrl+V` 粘贴图片入队，免去繁琐的上传步骤。
  - **实时监控**: 自动轮询后端状态，展示重试次数、Token 消耗及当前节点。
  - **数学渲染**: 完美支持大模型输出的 LaTeX 公式渲染（KaTeX）。
  - **人工介入工作台**: 当系统多次审核失败时，提供直观的双栏编辑器供管理员修复草稿并重新推入排版。

## 快速开始

### 1. 环境准备
确保你的系统已安装：
- Python 3.10+
- Node.js 18+ (推荐 20+)

### 2. 配置环境变量
在 `backend` 目录下创建 `.env` 文件，并配置你的大模型 API 密钥：

```bash
cd backend
touch .env
```

将以下内容填入 `.env` 文件（请替换为你自己的真实 API Key 和 Base URL，本系统默认使用兼容 OpenAI 格式的接口，如 GPT-4o 系列）：

```env
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
```

### 3. 一键启动服务 (推荐)
为了避免在多个终端中来回切换，你可以使用项目根目录下的 `start.sh` 脚本一键启动前端和后端：

```bash
# 赋予执行权限（仅需一次）
chmod +x start.sh

# 一键启动
./start.sh
```

*(按 `Ctrl+C` 即可同时关闭前端和后端服务)*

### 4. 手动分别启动（可选）
如果你想分别查看前后端的日志，也可以手动启动：

**启动后端：**
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**启动前端：**
```bash
cd frontend
npm install
npm run dev
```

### 5. Docker / 服务器部署（新增）
项目根目录现已提供与线上一致的容器化文件：

- `Dockerfile`
- `docker-compose.yml`
- `Makefile`
- `scripts/build_image.sh`
- `scripts/save_image.sh`
- `scripts/upload_image.sh`
- `scripts/deploy_remote.sh`

关键点：

- 镜像内已安装 `pandoc`，用于 DOCX 导出。
- 本地 compose 与线上 `zyb_agent` 容器环境保持一致。

本地构建与启动：

```bash
docker build -t zyb_agent:latest .
APP_PORT=35828 docker compose up -d
```

导出镜像包：

```bash
make build
make save
```

上传并部署到服务器：

```bash
SSH_PASSWORD='你的服务器密码' make upload
SSH_PASSWORD='你的服务器密码' make deploy
```

等价脚本方式：

```bash
./scripts/build_image.sh
./scripts/save_image.sh zyb_agent.tar.gz
SSH_PASSWORD='你的服务器密码' ./scripts/upload_image.sh zyb_agent.tar.gz
SSH_PASSWORD='你的服务器密码' ./scripts/deploy_remote.sh zyb_agent.tar.gz
```

## 如何使用流水线？

1. **访问看板**: 在浏览器中打开 `http://localhost:5173`。
2. **入队任务**:
   - 找一道数学题/物理题截图。
   - 在页面任意位置按下 `Ctrl+V` (或 `Cmd+V`)，图片将立即进入顶部的“待处理队列”。
   - 你可以继续截图并粘贴多张，它们会依次排队。
3. **开始处理**:
   - 点击蓝色的 `[开始处理]` 按钮。前端会将队列批量提交给后端。
   - 系统将自动并发处理任务。你可以在下方看到最新的任务详情，包含状态轮询。
4. **人工接管 (Manual Intervention)**:
   - 如果遇到极高难度的题目，Agent 两次审查（Reviewer）均判定为 `FAIL`，或者遇到严重的系统错误，任务状态将变为 `manual`（或者 `failed`）。
   - 此时，页面右侧会亮起红灯，展示 **审查失败的原因** 以及 **当前的解答草稿**。
   - 你可以直接在文本框内修正公式或逻辑错误，然后点击 `[Approve & Resume]`，系统会带着你的修正结果直接进入最终排版节点。
5. **获取结果**:
   - 当状态变为 `completed` 时，右侧将呈现包含漂亮数学公式的最终排版结果。

## 自动化接题控制台（新增）

后端新增独立命名空间 `automation`，并提供一套可串行执行的 API：

- `POST /api/automation/session/start`
- `POST /api/automation/scan/start`
- `GET /api/automation/tasks`
- `POST /api/automation/tasks/select`
- `POST /api/automation/grab/start`
- `POST /api/automation/solve/start`
- `POST /api/automation/task/{task_id}/review/save`
- `POST /api/automation/task/{task_id}/confirm-submit`
- `POST /api/automation/run/pause`
- `POST /api/automation/run/resume`
- `POST /api/automation/run/stop`（硬中断）
- `GET /api/automation/logs`

前端新增独立入口页面：

- `http://localhost:5173/automation-console.html`

注意事项：

1. 新增浏览器自动化依赖后，首次需要执行 `python -m playwright install chromium`。
2. `stop` 为硬中断，会取消当前运行中的后台任务并阻止后续步骤。
3. `review_pending` 默认超时 10 分钟自动流转到 `skipped`。
4. 默认启用 mock 浏览器模式：`AUTOMATION_USE_MOCK=1`。
5. 切换真实浏览器模式需配置：`AUTOMATION_USE_MOCK=0` 与 `AUTOMATION_TARGET_URL=<目标平台地址>`。

## 核心目录结构
```text
.
├── backend/                  # Python 后端目录
│   ├── app/
│   │   ├── api/              # API 路由
│   │   ├── agent/            # LangGraph 图引擎核心
│   │   │   ├── nodes/        # Solver, Reviewer, Formatter 节点逻辑
│   │   │   ├── graph.py      # 状态机拓扑连线
│   │   │   └── state.py      # 图状态数据结构
│   │   ├── models/           # SQLAlchemy 领域模型 & Pydantic 契约
│   │   ├── core/             # 数据库引擎与基础配置
│   │   └── main.py           # FastAPI 入口与后台任务编排
│   └── requirements.txt
├── frontend/                 # React 前端目录
│   ├── src/
│   │   ├── App.tsx           # 核心监控看板与交互逻辑
│   │   └── index.css         # Tailwind 全局样式
│   ├── package.json
│   └── tailwind.config.js
├── PRD.md                    # 产品需求文档
└── Architecture.md           # 架构设计文档
```
