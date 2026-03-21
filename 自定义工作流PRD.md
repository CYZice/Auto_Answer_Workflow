# 自定义工作流与单任务节点干预 PRD

## 背景与目标
**背景**：当前系统中（Solver -> Reviewer -> Formatter）工作流为全自动执行。一旦任务失败（如 Reviewer 审查不通过）或排版结果不合预期，用户只能重新从头跑一次完整任务，既浪费 Token，又缺乏人工干预和修正过程的手段。
**目标**：为特定的已有任务提供人工介入和单步跑批能力。允许用户输入关键上下文（图片 + 文本草稿），并灵活勾选希望执行的节点（例如：跳过解题，直接进行排版），以实现任务流的灵活拼装和失败恢复。

## 范围界定 (Scope Boxing)
**In Scope (包含)**：
- 单一已有任务详情页内的工作流节点复选操作。
- 节点执行合法性的前端图连通校验。
- 针对跳过前端节点（如仅跑 Formatter）时，展示额外的“草稿本”输入框供人工微调。

**Out of Scope (不包含)**：
- 全局任务流的拖拽式连线编辑器（此次只针对固定链条做局部截断和干预，不做大型的节点工作台）。
- 自定义新增各种不存在的新节点。

## User Stories (P0/P1 优先级)
**US1 (P0) - 节点选择与执行恢复**
作为一个教研人员/修改专员，当我在任务详情页发现排版有瑕疵时，我希望能只勾选 `[Formatter 排版]` 节点进行重试，这样我就可以跳过解题和审查，节省时间和成本。

**US2 (P0) - 前置条件动态输入**
作为一个干预者，当我不勾选前面的解题节点时，系统会自动展开一个“文本草稿框”，这样我就能把失败的草稿手动补充进去，供后续节点使用。

**US3 (P1) - 断连校验与防呆设计**
作为一个普通用户，当我错误地同时勾选了没有任何联系的 `[Solver]` 和 `[Formatter]` 且没有选中 `[Reviewer]` 时，系统会阻止提交并提示我节点必须连续，这样能防止发起的请求导致后端报错宕机。

## 详细功能需求 (Functional Requirements)
### 1. 交互增强：任务干预面板
- **入口**：在具体的某一条 Task 详情页（无论成功还是失败状态），增加 `[人工干预/特定节点重试]` 按钮。
- **界面元素**：
  - **多媒体展示区**：展示原任务的图片（ReadOnly）。
  - **节点选择器**：提供水平步骤条 Checkbox Group：`[ ] Solver解题 -> [ ] Reviewer审查 -> [ ] Formatter排版`。
  - **动态文本输入框**：如果用户**未勾选**第一步的 `Solver解题`，则下方**必须出现**且**必填**一个多行文本输入框（用于承载 `draft_text` 草稿参数）。

### 2. 场景枚举与校验逻辑 (Happy & Edge Cases)
| 场景 | 用户勾选节点组合 | 前端表现与校验结果 | 所需输入参数 |
| :--- | :--- | :--- | :--- |
| **Happy Path 1** | 全选：Solver -> Reviewer -> Formatter | 允许提交，执行全流程重试 | 图片 |
| **Happy Path 2** | 单节点：仅勾选 Formatter | 允许提交，展开动态文本框 | 图片 + 草稿文字 |
| **Happy Path 3** | 前截断：Solver -> Reviewer | 允许提交，到此终止 | 图片 |
| **Edge Case 1** | 中间断连：Solver + Formatter (未选 Reviewer) | **拒绝提交**，按钮置灰。Tooltip 提示：“工作流节点必须连续，请补齐中间的业务节点。” | / |
| **Edge Case 2** | 缺乏草稿：仅选 Formatter，未填草稿文字 | **拒绝提交**，输入框标红：“请通过此框输入排版前所需的文本草稿。” | 草稿文字不足 |

### 3. API 请求映射
在表单提交时，前端需将当前用户的勾选结果转换为后端可用的字段组发送给接口：
- 识别“最早的起始勾选节点”作为后端引擎的 `entry_point`。
- 将动态文本输入框的内容作为后端的 `draft_text`。

## 验收标准 (Acceptance Criteria)
**Scenario 1: 成功的单节点排版触发**
- Given 我位于任务详情页，且点击了干预重试
- When 我取消勾选 `Solver` 和 `Reviewer`
- And 勾选了 `Formatter`
- Then 页面下方出现必填的“文本草稿本”输入框
- When 我输入了一段长文本并点击确定执行
- Then 系统成功向后端下发带有 `entry_point="formatter"` 及其文字 payload 的网络请求

**Scenario 2: 非法的工作流阻断**
- Given 我位于状态重试面板
- When 我勾选了 `Solver` 和 `Formatter`
- And 我没有勾选 `Reviewer`
- Then 表单的“执行参数/提交”按钮变成不可点击状态 (Disabled)
- And Hover该按钮时出现文本提示：“执行流已断点，您必须同时勾选 Reviewer 节点”

## 数据字段定义 (Payload Variables)
- `entry_point` (String, Required): 后端图处理的入口节点。取值枚举：`["solver", "reviewer", "formatter"]`
- `target_nodes` (Array[String], Optional): 用户勾选的欲将运行的节点全量列表合集，方便后端做末端阶段截断使用。
- `draft_text` (String，Optional): 即使用户介入过程中的自定义草稿数据，如果不从 solver 走，必须传文本给大模型排版使用。

## 影响范围分析
- **后端模型层**: `backend/app/models/schemas.py` (扩展现有的 `ManualSubmitRequest` 请求体结构，支撑动态组合节点)。
- **后端状态层**: `backend/app/agent/state.py` (需在 `AgentState` 中补充对 `target_nodes` 的状态下发支撑，以供节点判断)。
- **后端路由层**: `backend/app/main.py` (修改 `submit_manual_review` 方法，处理定制的恢复请求，并向图引擎注入入口点)。
- **后端核心拓扑**: `backend/app/agent/graph.py` (按最小侵入性原则，仅在 `route_after_review` 方法中补充一处分支逻辑，若截断范围不含后续节点则直接流向 `END`，实现“执行到此终止”的功能)。
- **前端表现层**: `frontend/src/` (任务详情与重试干预面板，新增动态可视化步骤条与勾选联动逻辑)。

## 接口定义
根据最小侵入原则，复用人工接管的提交接口 `POST /api/tasks/{task_id}/manual`，兼容老历史且补充新字段。

**请求类型定义 (伪代码)**
```python
# 扩展 schemas.py - ManualSubmitRequest
class ManualSubmitRequest(BaseModel):
    action: Literal["resume", "skip_review", "fail", "custom_run"] = Field(
        description="新增 custom_run 模式表示由用户界面自选图节点恢复执行"
    )
    draft_solution: Optional[str] = Field(None, description="人工修正后的解题/草稿内容")
    entry_point: Optional[Literal["solver", "reviewer", "formatter"]] = Field(
        None, description="自定义起点"
    )
    target_nodes: Optional[List[str]] = Field(
        None, description="运行的候选节点范围，例如['solver', 'reviewer']用于中途截断"
    )
```

**内部状态变更 (伪代码)**
```python
# 扩展 state.py - AgentState
class AgentState(TypedDict):
    # 此处省略原有字段...
    target_nodes: Optional[List[str]]  # 注入期望拦截点
```

## 实施步骤 (Task List)
- [ ] **[Step 1] 修改后端模型参数防断裂**
  更新 `backend/app/models/schemas.py`，将上述 `entry_point` 与 `target_nodes` 追加至 `ManualSubmitRequest`。
- [ ] **[Step 2] 状态机拓扑适配末端截断机制**
  更新 `backend/app/agent/state.py` 追加 `target_nodes`；同时修改 `backend/app/agent/graph.py` 的路由中心 `route_after_review`，在 `PASS` 分支时判定，如果 `target_nodes` 存在且不包含 `"formatter"`，抛弃原流程映射至预先设定的 `END` 结束位以安全跳出工作流。
- [ ] **[Step 3] 路由调度下发自拼装流转指令**
  修改 `backend/app/main.py` 中的 `submit_manual_review` 接口实现。识别 `action == "custom_run"`。
  - 将用户 `req.draft_solution` 落入对应 task 的 JSON History 兜底字段内；
  - 构造 `thread_initial_state` 装配 `target_nodes`；
  - 最终使用指定的 `req.entry_point` 为参数启动 `build_graph(entry_point=...)` 或传递给 `run_agent_workflow_async` 进行子图恢复执行。
- [ ] **[Step 4] 前端交互设计与防呆校验**
  开发或修改前端对应的该条发包组件。
  - **动态渲染**: 点击重试则显示 `Checkbox(Solver)` -> `Checkbox(Reviewer)` -> `Checkbox(Formatter)`。
  - **防呆拦截 (GuardClause)**: 校验 Array.indexOf 索引，强制判断连续性。不连续则阻塞 `Submit` 触发并红色 `Toast` 警告。
  - **传参组装**: 若校验通过，求首元素为 `entry_point`，数组全量为 `target_nodes`，附带必填验证过的 `draft_text` (映射为 `draft_solution`) 一并下发上述扩充后的 `/manual` API。
