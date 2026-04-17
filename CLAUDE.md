# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Zyb-Agent 是一个基于大语言模型（GPT-4o-mini）和 LangGraph 的自动化题目解析流水线系统，支持多模态图片批量上传、自动识图解题、结构化机器审查和自动排版。

**技术栈**:
- 后端: FastAPI + LangGraph + SQLite (WAL 模式) + SQLAlchemy
- 前端: Vite + React + Tailwind CSS + TanStack Query
- 浏览器自动化: Playwright

## 快速启动

```bash
# 一键启动前端 + 后端
./start.sh

# 手动分别启动
# 后端
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8080
# 前端
cd frontend && npm run dev
```

**访问地址**:
- 前端看板: http://localhost:5173
- 自动化控制台: http://localhost:5173/automation-console.html
- 后端 API: http://localhost:8080

## 核心架构

### 主任务流水线 (Agent Pipeline)

状态机流转: `queued → solving → reviewing → formatting → completed`

```
Solver (解题) → Reviewer (审查) → Formatter (排版)
                     ↓
               FAIL → Solver (重试一次)
                     ↓
               两次FAIL → failed/manual
```

核心文件:
- `backend/app/agent/state.py` - AgentState 定义
- `backend/app/agent/graph.py` - LangGraph 拓扑编排
- `backend/app/agent/nodes/` - solver.py, reviewer.py, formatter.py

### 自动化接题系统 (Automation)

独立的状态机控制浏览器自动化任务抓取和提交流程:

状态: `discovered → selected → grabbed → solving → filled → review_pending → ready_to_submit → submitting → submitted`

核心文件:
- `backend/app/automation/service.py` - 自动化服务核心
- `backend/app/automation/state_machine.py` - 状态转换规则
- `backend/app/automation/browser_worker.py` - Playwright 浏览器控制
- `backend/app/api/automation_routes.py` - 自动化 API 路由

### 数据模型

- `backend/app/models/domain.py` - SQLAlchemy 模型 (Task, AgentLog)
- `backend/app/models/schemas.py` - Pydantic 契约 (API 请求/响应)
- `backend/app/automation/models.py` - 自动化任务模型

### 前端结构

- `frontend/src/App.tsx` - 主看板页面
- `frontend/src/automation-console/App.tsx` - 自动化控制台页面
- `frontend/src/automation-console/api.ts` - 自动化 API 调用

## 环境变量配置

在 `backend/` 目录创建 `.env`:

```env
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
AUTOMATION_USE_MOCK=1          # 自动化 mock 模式 (默认1)
AUTOMATION_TARGET_URL=          # 真实浏览器目标地址
```

## 数据库

SQLite WAL 模式存储于 `backend/agent_tasks.db`，使用 SQLAlchemy ORM 操作。数据库在 FastAPI 启动时通过 lifespan 自动初始化（`Base.metadata.create_all`），并通过 `ensure_task_preview_columns` 兼容老库补列。

## 测试

```bash
# 后端测试（使用项目 .venv）
cd backend && source .venv/bin/activate
pytest tests/ -v                    # 运行所有测试
pytest tests/test_agent_solver_and_graph.py -v   # 运行单文件测试

# 前端测试
cd frontend
npm run lint                        # ESLint 检查
```

## 工作流恢复机制

任务失败后可通过 `POST /api/tasks/{task_id}/manual` 接口从指定节点恢复执行：
- `resume` action：从 `failed_node` 恢复（自动重试 solver）
- `skip_review` action：跳过 reviewer，直接进入 formatter（需保证 `draft_solution` 已存在）
- `custom_run` action：从指定 `entry_point` 和 `target_nodes` 自定义节点链执行

支持恢复的节点：`solver`、`reviewer`、`formatter`。Workflow 顺序为 `["solver", "reviewer", "formatter"]`，从非 solver 节点恢复时该节点之前的节点结果必须已存在于 `history` 中。

## 核心状态机

**主任务流水线状态**：`queued → solving → reviewing → formatting → completed`

LangGraph 拓扑中三个节点的路由规则：
- `solver` 后：`status` 为 `reviewing` 则进入 `reviewer`，否则结束
- `reviewer` 后：`review_decision=PASS` 进入 `formatter`；`review_decision=FAIL` 且 `retry_count<1` 重试 `solver`；否则进入 `failed` 终态
- `formatter` 后：直接进入 `completed` 终态

**自动化接题系统状态**：`discovered → selected → grabbed → solving → filled → review_pending → ready_to_submit → submitting → submitted`

## 并发控制

全局信号量 `task_semaphore`（默认容量 5）限制同时执行的大模型推理任务数，防止触发 API Rate Limit。

## 事件流

`app.core.events.task_events` 是全局流式事件总线，SSE 端点 `GET /api/tasks/{task_id}/stream` 监听此总线向前端推送模型思考过程（`on_chat_model_stream` 事件）和节点切换事件（`node_start` 事件）。

## 环境变量

在 `backend/` 目录创建 `.env`：

```env
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
AUTOMATION_USE_MOCK=1          # 自动化 mock 模式 (默认1)
AUTOMATION_TARGET_URL=          # 真实浏览器目标地址
```

## 开发命令

```bash
# 前端
cd frontend
npm run dev      # 开发服务器
npm run build    # 构建生产版本
npm run lint     # ESLint 检查

# 后端 (使用项目 .venv)
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload   # 热重载开发
```

## 项目文件组织

```
backend/
├── app/
│   ├── main.py                  # FastAPI 入口，路由定义，workflow 驱动
│   ├── agent/
│   │   ├── state.py             # AgentState TypedDict 定义
│   │   ├── graph.py             # LangGraph 拓扑构建
│   │   └── nodes/
│   │       ├── solver.py        # 解题节点（LLM 调用）
│   │       ├── reviewer.py       # 审查节点（答案质量判定）
│   │       └── formatter.py     # 排版节点（Markdown 格式化）
│   ├── automation/              # 浏览器自动化接题系统
│   │   ├── service.py
│   │   ├── state_machine.py
│   │   └── browser_worker.py
│   ├── api/
│   │   ├── routes.py            # 任务管理 API
│   │   ├── automation_routes.py # 自动化 API
│   │   └── mineru_routes.py     # MinerU 解析 API
│   ├── models/
│   │   ├── domain.py            # SQLAlchemy ORM 模型
│   │   └── schemas.py           # Pydantic 请求/响应模型
│   ├── core/
│   │   ├── database.py          # SQLAlchemy 引擎和会话管理
│   │   └── events.py            # SSE 全局事件总线
│   └── services/
│       └── runtime_config.py    # 提示词模板和运行时配置
└── requirements.txt

frontend/
├── src/
│   ├── App.tsx                  # 主看板页面
│   └── automation-console/
│       ├── App.tsx              # 自动化控制台页面
│       └── api.ts              # 自动化 API 调用封装
└── package.json
```
