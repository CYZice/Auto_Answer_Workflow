# 智能题目解析 Agent 自动化流水线 架构设计文档

## 1. 影响范围分析（核心文件规划）
我们将采用前后端分离的 Monorepo 结构。后端的逻辑引擎（LangGraph）与 API 接入层（FastAPI）严格解耦，确保状态机的每一次流转都能可靠地持久化到 SQLite 中。

*   **`backend/app/models/`** (数据层)
    *   `domain.py`: SQLAlchemy 数据库模型（持久化 Task 状态、历史、Token）。
    *   `schemas.py`: Pydantic 模型（定义 API 请求/响应接口、Agent 节点间的强类型契约，特别是 Reviewer 的结构化输出）。
*   **`backend/app/agent/`** (核心图引擎)
    *   `state.py`: LangGraph 的 `AgentState` 定义，确保状态流转可控。
    *   `graph.py`: 核心状态机拓扑编排（定义 Nodes 和 Edges，处理重试与中断）。
    *   `nodes/`: 包含 `solver.py`, `reviewer.py`, `formatter.py`。
*   **`backend/app/api/`** (接口层)
    *   `routes.py`: FastAPI 路由，负责与 LangGraph 的交互（触发任务、恢复挂起任务）。

## 2. 核心数据流/接口定义

为了杜绝“自由发挥”，我们在此严格定义核心输入输出契约（Pydantic Schemas）：

### A. API 接口定义
*   `POST /api/tasks` -> `TaskCreateResponse(task_id, status="queued")`
*   `GET /api/tasks/{task_id}` -> `TaskDetailResponse(...)`
*   `POST /api/tasks/{task_id}/manual` (提交人工修改) -> `ManualSubmitRequest(draft_solution: str, action: Enum["resume", "fail"])`

### B. 核心契约文件 1：Pydantic 接口定义 (schemas.py)
```python
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from enum import Enum

# --- Agent Reviewer 结构化输出契约 ---
class Issue(BaseModel):
    type: str = Field(description="问题类型，如：计算错误、逻辑跳跃、格式问题")
    detail: str = Field(description="具体问题描述")

class ReviewDecision(BaseModel):
    decision: Literal["PASS", "FAIL"] = Field(description="审查结论")
    reason: Optional[str] = Field(None, description="整体原因概括（FAIL 时必填）")
    issues: Optional[List[Issue]] = Field(default_factory=list, description="具体错误点列表")

# --- API 请求与响应契约 ---
class TaskStatus(str, Enum):
    QUEUED = "queued"
    SOLVING = "solving"
    REVIEWING = "reviewing"
    FORMATTING = "formatting"
    MANUAL = "manual"
    COMPLETED = "completed"
    FAILED = "failed"

class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskStatus

class ManualSubmitRequest(BaseModel):
    action: Literal["resume", "fail"] = Field(description="resume表示继续排版，fail表示放弃")
    draft_solution: Optional[str] = Field(None, description="人工修正后的解题内容")
```

### C. 核心契约文件 2：LangGraph 状态机定义 (state.py)
```python
from typing import TypedDict, Optional, Literal

class AgentState(TypedDict):
    """
    LangGraph 图引擎流转的核心状态。
    每一次节点执行都会更新此状态，并持久化到 SQLite 中。
    """
    task_id: str
    image_url: str
    
    # 当前流程的内部状态
    status: Literal["queued", "solving", "reviewing", "formatting", "manual", "completed", "failed"]
    
    # 节点流转数据
    draft_solution: Optional[str]      # Solver 输出的草稿 / 人工编辑后的草稿
    review_decision: Optional[str]     # PASS / FAIL
    review_feedback: Optional[str]     # Reviewer 给出的综合反馈（用于重试）
    final_result: Optional[str]        # 最终交付的 Markdown
    
    # 策略控制数据
    retry_count: int                   # 当前重试次数（第 2 次审查仍失败即 failed）
    error_msg: Optional[str]           # 系统错误信息
    total_tokens: int                  # 累计消耗的 Token
```

## 3. 分步实施步骤

*   **Step 1: 基础设施与数据层搭建 (Data Layer)**
    *   初始化 FastAPI 框架。
    *   实现 SQLite (WAL模式) + SQLAlchemy，完成 `Task` 表结构的建立。
    *   **测试标准**：可以通过 API 成功向数据库插入一条 Task 记录并查询。
*   **Step 2: 图状态机与节点脚手架搭建 (Graph & Nodes)**
    *   定义 `AgentState` 和所有节点的空函数（Mock 返回值）。
    *   在 `graph.py` 中连线（Edges），实现 `solving -> reviewing -> (条件判断) -> formatting / solving / manual`。
    *   **测试标准**：编写单元测试，手动输入模拟图状态，断言拓扑流转（包括重试 1 次进 failed、超时进 manual）符合 PRD 预期。
*   **Step 3: 大模型接入与结构化输出 (LLM Integration)**
    *   接入大模型 API（GPT-4o / Claude 3.5）。
    *   实现 Node B (Reviewer)，强制绑定 `ReviewDecision` Pydantic Schema。
    *   **测试标准**：输入一张错误解答图，Reviewer 稳定输出 JSON 格式的 `FAIL` 及 `issues`。
*   **Step 4: API 与图引擎绑定 & 并发控制 (API Binding)**
    *   使用 `asyncio.Semaphore(5)` 包装任务触发接口。
    *   实现 `/manual` 接口调用 LangGraph 的 `update_state` 恢复被挂起的流程。
    *   **测试标准**：通过 API 上传 10 张图，观察仅有 5 个并发执行；手动通过 API 提交修改，任务成功恢复进入排版。
*   **Step 5: 前端工程初始化 (Frontend Setup)**
    *   使用 Vite + React + Shadcn UI 初始化面板。
    *   集成 React Query 进行 `/api/tasks` 轮询。
