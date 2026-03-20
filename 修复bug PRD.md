*   通过监听 `on_chat_model_stream` 事件，将大模型的 `chunk`（包括 `<think>` 标签内容和正文）实时 `yield` 给前端。
*   **前端主界面展示**：前端主界面通过解析该 SSE 流数据，提取 `<think>...</think>` 内的思考过程（可实现类似 ChatGPT 的折叠展示），并将最终结果以打字机效果呈现。
*   在 LangGraph 的每个 Node 执行前后（或利用 LangChain 的 Callbacks），自动将 LLM 的输入和输出落库。
*   **前端后台日志查看**：除了 SQLite 底层落库外，将新增诸如 `GET /api/logs` 等接口，使得在前端后台界面（Admin Panel）可以按 `task_id` 查询并可视化展示每个节点的详细请求参数、响应体以及 Token 消耗。
### 🔍 当前架构诊断 (As-Is)

根据对 `backend/app/main.py`、`graph.py`、`state.py` 和 `llm_client.py` 的探查，我回答了之前提出的 4 个澄清问题：

1.  **工作流底座**：你使用的是 **LangGraph (`StateGraph`)**。目前在 `main.py` 中通过 `asyncio.to_thread(graph_app.invoke, initial_state)` 一次性跑完整个图。**痛点**：当前并没有使用 LangGraph 的 Checkpointer 机制，状态只是在工作流**彻底结束或崩溃后**才写入 SQLite 的 `Task` 表，这就导致中途崩溃无法做到“节点级断点恢复”。
2.  **流式传输**：目前前端和后端的通信完全是**普通的 HTTP 轮询** (`GET /api/tasks/{task_id}`)。代码里没有任何 SSE (Server-Sent Events) 或 WebSocket 的实现。
3.  **Token 溢出 Bug**：你在 `llm_client.py` 中使用了 `langchain_openai.ChatOpenAI`，并在 Reviewer 节点使用了 `.with_structured_output(ReviewDecision)`。**破案了**：当使用结构化输出时，如果遇到较弱的模型或复杂图片，模型可能会在 JSON 格式化时陷入死循环（一直在补全括号或重复输出某段推理），从而无视你设置的 `max_tokens`（部分 API 提供商对工具调用的 token 截断处理有 bug），最终爆出 1-2 万的 token。
4.  **日志存储**：目前只有一张 `Task` 表，且只在 `history` 字段里存了最终的草稿和 Review 结论的 JSON，**完全没有**单次 LLM Request/Response 的明细日志表。

---

### 🎯 最终需求确认与落地策略 (To-Be)

基于上述诊断，我已经消除了所有“模糊性”。如果你同意，接下来的开发任务（User Stories）将严格按照以下技术路线执行：

#### 1. 节点级断点恢复 (State Recovery)
*   **弃用** `graph_app.invoke` 跑到底的做法。
*   **引入** LangGraph 的内置 Checkpointer（例如 `MemorySaver` 或 `SqliteSaver`），在编译图时传入 `graph.compile(checkpointer=...)`。
*   这样每次 Node 执行完，LangGraph 会自动帮你把当前状态（包括 `draft_solution`, `retry_count`）持久化。恢复时，只需要传入 `{"configurable": {"thread_id": task_id}}` 即可从失败节点继续。

#### 2. 实时流式输出 (Streaming & Thinking)
*   因为你用的是 FastAPI，我们将引入 **SSE (Server-Sent Events)** 接口，比如新增 `GET /api/tasks/{task_id}/stream`。
*   将 `graph_app.invoke` 替换为 `graph_app.astream_events`（LangChain 的事件流 API）。
*   通过监听 `on_chat_model_stream` 事件，将大模型的 `chunk`（包括 `<think>` 标签内容和正文）实时 `yield` 给前端。

#### 3. 结构化运行日志 (Execution Logging)
*   在 `app.models.domain` 中新增一张表 `AgentLog` (包含 `task_id`, `node_name`, `request_payload`, `response_payload`, `cost_tokens`)。
*   在 LangGraph 的每个 Node 执行前后（或利用 LangChain 的 Callbacks），自动将 LLM 的输入和输出落库。

#### 4. Token 溢出 Bug 彻底修复 (Token Bug Fix)
*   **硬熔断机制**：除了 `max_tokens=4096`，必须在 `ChatOpenAI` 初始化时强行增加 `stop=["</think>", "```json\n\n\n"]` 等停止词防重。
*   **异常捕获**：在 `reviewer.py` 和 `solver.py` 中增加超时控制（如 `timeout=60`）。如果单次生成时间过长，强制抛出异常并进入重试/失败节点，而不是任由其跑到 2 万 Token。
