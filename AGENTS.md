# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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

SQLite WAL 模式存储于 `backend/agent_tasks.db`，使用 SQLAlchemy ORM 操作。

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
