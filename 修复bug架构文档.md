## 影响范围分析 (Context Pruning)
Coder **只需要**阅读和修改以下文件，请不要修改任何其他无关模块以避免引入幻觉和回归风险：

1. **`backend/app/models/domain.py`** (新增日志表结构)
2. **`backend/app/agent/graph.py`** (引入 LangGraph Checkpointer)
3. **`backend/app/main.py`** (引入 SSE 接口，修改执行逻辑)
4. **`backend/app/agent/nodes/llm_client.py`** (修复 Token 溢出与防重词)
5. **`backend/app/agent/nodes/solver.py` & `reviewer.py`** (引入超时控制与调用方日志)

> **依赖分析**：
> 需要确保已安装 `langgraph-checkpoint-sqlite` 以支持状态持久化，如果项目未包含该依赖，请 Coder 先执行 `pip install langgraph-checkpoint-sqlite`。SSE 可以通过 FastAPI 的 `StreamingResponse` 和 `asyncio` 直接实现，无需引入外部依赖。

---

## 接口定义 (Data Flow Design)

### 1. 数据库模型定义 (AgentLog)
```python
# app.models.domain.py
class AgentLog(Base):
    __tablename__ = "agent_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, index=True)
    node_name = Column(String) # solver / reviewer / formatter
    request_payload = Column(Text) # JSON string
    response_payload = Column(Text) # JSON string
    cost_tokens = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

### 2. SSE 流式接口与后台日志查询 (FastAPI)
```python
# app.main.py
@app.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str):
    """
    Returns: Server-Sent Events (SSE)
    Format: data: {"event": "on_chat_model_stream", "chunk": "...", "node": "solver"}
    Description: 包含模型的流式输出（含思考过程）。前端主界面通过解析该流，提取 <think> 标签内部的文本作为折叠的“思考过程”，其余内容作为“最终输出结果”实时渲染。
    """
    # 伪代码：依赖 graph_app.astream_events 或 async generator
    pass

@app.get("/api/logs")
async def get_logs(task_id: str = None):
    """
    Returns: List of AgentLog
    Description: 供前端后台使用，通过此接口在管理界面展示模型的完整请求、响应明细及 Token 消耗等。
    """
    pass
```

---

## 实施步骤 (Task List)

请 Coder 严格按照以下原子步骤执行：
*   [ ] **[Step 1] 完善数据模型与日志记录 (AgentLog)**
    *   在 `backend/app/models/domain.py` 中新增 `AgentLog` 表。
    *   确保在 FastAPI 启动生命周期 `Base.metadata.create_all` 时能够自动创建此表。
*   [ ] **[Step 3] 引入 LangGraph 状态机断点恢复 (Checkpointer)**
    *   修改 `backend/app/agent/graph.py` 的 `build_graph()`。
    *   引入 `from langgraph.checkpoint.sqlite import SqliteSaver`（或 `MemorySaver` 作为前期过渡）。
    *   在 `return workflow.compile(checkpointer=memory)` 时挂载 Checkpointer。
    *   在 `backend/app/main.py` 的 `run_agent_workflow_async` 中，调用 `graph_app.invoke` 时，必须传入 `config={"configurable": {"thread_id": task_id}}`。
*   [ ] **[Step 4] 提供 SSE 实时流式传输接口 (Streaming)**
    *   在 `backend/app/main.py` 中新增路由 `GET /api/tasks/{task_id}/stream`。
    *   使用 `FastAPI` 的 `StreamingResponse`。
    *   使用 LangGraph 的 `astream_events` 监听底层的 `on_chat_model_stream` 事件。将 `chunk.content` 实时通过 SSE 格式 `yield` 返回给前端。
*   [ ] **[Step 5] 在工作流中埋点写入 Execution Logging 并提供查询接口**
    *   在每个节点（Solver / Reviewer）的执行代码中，或通过 LangChain 的 Callback Handler，将每次对话的 `messages` 数组和输出内容序列化后写入 `AgentLog` 表中。
    *   在 `backend/app/main.py` 新增 `GET /api/logs` 接口，以便在前端后台查询并展示单次执行节点的完整交互日志。