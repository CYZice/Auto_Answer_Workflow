# 智能题目解析 Agent 自动化流水线 PRD（V2.1）

## 0. 文档目的
- 将“识图解题 -> 自动审查 -> 自动纠错(最多 N 次) -> 自动排版 -> 失败转人工”定义为可实现、可验收的产品需求
- 明确任务状态、数据留存、监控视图与人工接管边界，减少实现歧义

## 0.1 已确认的关键默认值（如需可再调整）
- 并行生产线：默认 5（可配 3–10）
- 审查失败重做：允许 1 次重做（即最多 2 次审查）；第二次审查仍 FAIL -> failed
- 单题超时：5 分钟，超时 -> manual
- 系统错误（如 provider_error）：自动重试 1–2 次；仍失败 -> manual
- 权限：单一管理员账号
- 图片存储：仅本地存储路径
- 成本：暂不计算与展示成本；仅统计并返回 token
- 交付格式：Markdown（包含 LaTeX 公式标记）
- 多图上传：每张图独立任务

---

### 第一部分：需求规格说明书 (PRD)

#### 1. 项目目标
- 在“低题量（单次约 10–20 题）”场景下，提供高稳定、可追溯的题目解析流水线
- 支持 3–5（默认 5）条并行处理，任务可观测、可恢复（崩溃重启后可继续未完成任务）
- 实现闭环：识图解题 -> 自动审查 ->（必要时重做 1 次）-> 自动排版 -> 交付
- 对“超时/不可识别/系统错误”等非审查型失败提供人工接管：查看全量历史、编辑/确认结果、继续流转或终止

#### 1.1 范围（In Scope）
- 输入：单图/多图上传；每张图对应 1 个独立任务（Task）
- 输出：每题 1 份最终 Markdown（含数学公式标记），并可下载/复制
- 过程：自动节点处理、超时/不可识别/系统错误转人工、人工提交后可继续排版
- 可观测：实时看板、任务列表、单任务全量历史与 token

#### 1.2 非范围（Out of Scope）
- 不做题库/知识点结构化入库、相似题检索、用户账号体系的复杂权限模型（如 RBAC）
- 不承诺“0 人工”；目标是把人工比例控制在可管理范围（指标见验收标准）

#### 2. 核心业务流程（Workflow）

#### 2.1 术语与对象
- 任务（Task）：与“1 张题目图片”一一对应的处理单元
- 任务线程（Thread_ID）：用于串联该任务的多轮尝试与所有节点输出的逻辑标识（必须全局唯一）
- 生产线（Worker Slot）：并行处理的执行槽位，数量可配置

#### 2.2 状态机（必须落库）
- 任务状态枚举（state）：
  - queued：已入队等待处理
  - solving：解题中
  - reviewing：审查中
  - formatting：排版中
  - manual：需要人工处理（挂起）
  - completed：已完成
  - failed：系统性失败（不可恢复/人工放弃）
- 关键转移：
  - queued -> solving -> reviewing
  - reviewing: pass -> formatting -> completed
  - reviewing: fail -> solving（仅允许 1 次重做；第二次审查仍 FAIL -> failed）
  - unrecognizable/timeout -> manual
  - error（系统/供应商错误）-> 自动重试 1–2 次 -> manual
  - 管理员例外操作：failed 可转 manual（用于人工修复后重新进入工作流）

#### 2.3 节点职责与输入输出
- Node A（Solver）
  - 输入：题目图片 + 该任务历史（如有）+ 审查反馈（如有）
  - 输出：结构化“解题结果草稿”（含答案与推导）或 UNRECOGNIZABLE
- Node B（Reviewer）
  - 输入：题目图片 + Solver 草稿
  - 输出：PASS/FAIL + 失败原因（可直接用于指导下一次重做）
- Node C（Formatter）
  - 输入：最终通过的解题内容
  - 输出：最终交付 Markdown（包含数学公式标记与版式规范）

#### 2.4 重试、失败与人工接管
- 审查失败重做策略（审查处收口）：
  - 第 1 次审查 FAIL -> 触发重做（回到 solving）并进入第 2 次审查
  - 第 2 次审查仍 FAIL -> 进入 failed（停止自动处理）
- 进入人工（manual）的触发条件（任一满足）：
  - UNRECOGNIZABLE
  - timeout（5 分钟）
  - error（系统/供应商错误：自动重试 1–2 次仍失败）
  - 管理员将 failed 转 manual（人工修复入口）
- 人工动作与回流（口径澄清）：
  - 管理员编辑“解题内容（相当于 Solver 的输出草稿）”并提交
  - 提交后任务重新从头进入工作流：solving -> reviewing -> formatting（历史保留，形成新一轮尝试）
  - 放弃处理则标记为 failed

#### 2.5 恢复能力（断点续传需求）
- 系统重启后：所有非 completed/failed 的任务必须能恢复到“可继续处理”的状态（至少恢复到 queued 或 manual）

#### 3. 监控与管理需求（Web 管理台）

#### 3.1 页面与模块
- Dashboard（实时看板）
  - 显示并行槽位数与每个槽位当前任务：task_id、缩略图、state、开始时间、耗时
  - 聚合指标：今日处理数、成功数、人工数、失败数、总 token
- Tasks（任务列表）
  - 必备字段列：task_id、state、retry_count、更新时间、耗时、token、操作
  - 过滤：按 state、时间范围、是否需要人工
- Task Detail（任务详情）
  - 展示：原图、每一轮 Solver 输出、Reviewer 结果与原因、Formatter 输出、错误信息（如有）
  - 展示：累计 token（按节点/轮次聚合）
- Manual Queue（人工队列）
  - 列表：所有 state=manual 的任务
  - 详情：左侧原图；右侧可编辑“解题内容”；按钮：提交排版 / 标记失败

#### 3.2 可用性要求
- 刷新策略：页面可自动刷新或手动刷新（默认自动刷新）
- 审计：所有人工提交必须记录操作者与时间（见数据结构）

---

### 第二部分：技术选型建议

基于低题量、快速开发和断点续传的需求，推荐以下技术架构组合：

#### 1. 后端架构 (核心引擎)
*   **逻辑引擎：LangGraph (基于 LangChain)**
    *   **理由**：这是目前处理“循环重试”逻辑的业界标准。它原生支持 **Persistence (持久化)**，能把 Agent 的每一步自动存入数据库。如果程序崩了，重启后它知道刚才进行到哪一次重试了。
*   **异步框架：FastAPI**
    *   **理由**：轻量级、高性能，完美支持 Python 的异步调用，适合处理长耗时的 LLM 请求。
*   **数据库：SQLite (开启 WAL 模式)**
    *   **理由**：对于十几题的量，SQLite 性能绰绰有余。它不需要配置数据库服务器，一个文件即走，且与 LangGraph 兼容性极佳。

#### 2. 模型接入层
*   **多模态模型：GPT-4o 或 Claude 3.5 Sonnet**
    *   **理由**：这两者是目前识图（OCR）和逻辑推理最强的模型，能极大降低人工介入率。
*   **Token 统计：内部计数器**
    *   **实现口径**：记录模型返回的 token 用量（不做成本换算与展示）。

#### 3. 前端架构 (Web UI)
*   **核心选型：Vite + React + Shadcn UI + TanStack Query**
    *   **理由**：最适合内部工具的黄金搭档。Vite 极速轻量；Shadcn UI 提供开箱即用的专业组件（免调 CSS）；TanStack Query 自动处理前端对后台任务状态的轮询（Polling）与缓存更新。
*   **数学渲染：React-Markdown + Remark-Math + Rehype-Katex**
    *   **理由**：将大模型输出的 LaTeX 直接渲染为漂亮公式，人工审查刚需。

---

### 第三部分：系统数据结构设计（Schema，需求级）

目标：支撑状态机、全链路回溯、token 统计与人工审计。

| 字段 | 说明 |
| :--- | :--- |
| task_id | 唯一主键 |
| thread_id | 任务线程标识（用于串联多轮尝试） |
| image_url | 图片存储位置（本地路径或对象存储 URL） |
| state | queued/solving/reviewing/formatting/manual/completed/failed |
| retry_count | 当前已重试次数 |
| history | JSON：按时间记录每节点输入/输出摘要、审查结论、错误信息 |
| final_result | 最终交付 Markdown |
| token_usage | JSON：按轮次/节点聚合的输入/输出 token |
| error_code | 失败/异常分类（如 unrecognizable/timeout/provider_error 等） |
| created_at | 创建时间 |
| updated_at | 更新时间 |
| manual_operator | 人工处理人（如有） |
| manual_updated_at | 人工提交时间（如有） |

---

### 第四部分：边缘情况与约束（需求级）

1. **并发限制**：并行槽位固定为 N（默认 5），超出进入排队，避免触发模型侧限流并保证管理台可用性。
2. **人工接管（挂起与继续）**：任务进入 manual 时流程必须可被“挂起”；管理员提交人工编辑内容后，任务必须可从 manual 继续进入 formatting 并完成。
3. **公式渲染**：管理台与交付结果必须支持 LaTeX 公式渲染，否则人工审查与交付不可用。

### 第五部分：验收标准（MVP）
- 批量上传：一次上传 1–20 张图片，每张图生成 1 个独立任务并进入 queued
- 并发处理：同时最多占用 N 个生产线槽位（默认 5），其余排队
- 闭环完成：审查通过的任务进入 completed，并产出 final_result（Markdown + LaTeX 公式标记）
- 审查收口：第 1 次审查 FAIL 触发重做；第 2 次审查仍 FAIL 进入 failed
- 审查结构化：每次 Reviewer 输出均能通过 Schema 校验并被系统识别为 PASS/FAIL（不依赖自然语言解析）
- 超时处理：单题超过 5 分钟进入 manual
- 系统错误：自动重试 1–2 次；仍失败进入 manual
- 人工接管：manual/failed 任务可由管理员编辑后重新从头进入工作流，或标记 failed
- 可恢复：服务重启后，未完成任务仍可继续处理（不丢历史与 token 统计）
- Token 可见：每任务展示累计 token，Dashboard 展示汇总
