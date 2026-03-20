from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import uuid
import asyncio
import json
import os
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

from app.core.database import engine, Base, get_db
from app.models.domain import Task, AgentLog
from app.models.schemas import (
    TaskCreateRequest, TaskCreateResponse, TaskDetailResponse, 
    TaskStatus, ManualSubmitRequest
)
from app.agent.graph import build_graph

# 全局并发信号量，控制同时进行的大模型推理任务数（根据 PRD 要求默认为 5）
MAX_CONCURRENT_TASKS = 5
task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# 全局流式事件总线，用于将后台工作流的流式输出分发给所有 SSE 客户端
from collections import defaultdict
class TaskEventBus:
    def __init__(self):
        self.queues = defaultdict(list)
        
    def subscribe(self, task_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        self.queues[task_id].append(q)
        return q
        
    def publish(self, task_id: str, event_data: str):
        if task_id in self.queues:
            for q in self.queues[task_id]:
                q.put_nowait(event_data)
                
    def close(self, task_id: str):
        if task_id in self.queues:
            for q in self.queues[task_id]:
                q.put_nowait(None)
            del self.queues[task_id]

task_events = TaskEventBus()

# 提前编译好全局唯一的图引擎实例
graph_app = build_graph()

async def run_agent_workflow_async(task_id: str, db: Session):
    """
    异步执行图引擎工作流，并持久化每一步的状态。
    加入信号量以控制并发，防止触发模型 API 的 Rate Limit。
    """
    async with task_semaphore:
        print(f"[{task_id}] Acquired semaphore. Starting workflow...")
        
        # 1. 查询数据库获取任务初始信息
        task_record = db.query(Task).filter(Task.task_id == task_id).first()
        if not task_record:
            print(f"[{task_id}] Error: Task not found in DB.")
            return

        # 2. 构造图引擎的初始状态
        # 尝试从 history 中提取动态模型配置
        agent_configs = {}
        try:
            if task_record.history:
                history_data = json.loads(task_record.history)
                agent_configs = {
                    "solver": history_data.get("solver_config", {}),
                    "reviewer": history_data.get("reviewer_config", {}),
                    "formatter": history_data.get("formatter_config", {})
                }
        except Exception:
            pass

        initial_state = {
            "task_id": task_record.task_id,
            "image_url": task_record.image_url,
            "status": task_record.state,
            "retry_count": task_record.retry_count,
            "total_tokens": 0,
            "agent_configs": agent_configs
        }
        
        # 3. 运行图引擎
        try:
            config = {"configurable": {"thread_id": task_id}}
            async for event in graph_app.astream_events(initial_state, config=config, version="v2"):
                if event["event"] == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    node = event.get("metadata", {}).get("langgraph_node", "unknown")
                    data = json.dumps({
                        "event": "on_chat_model_stream",
                        "chunk": chunk.content,
                        "node": node
                    }, ensure_ascii=False)
                    task_events.publish(task_id, data)
            
            # 工作流执行完毕，获取最终状态
            state_tuple = graph_app.get_state(config)
            final_state = state_tuple.values
            task_events.close(task_id)
            
            # 4. 工作流结束，将最终状态落库
            # 必须重新获取 session 里的 task_record，防止多线程下 session 过期或 detached
            task_record = db.query(Task).filter(Task.task_id == task_id).first()
            if not task_record: return

            # 如果在执行期间被标记为 cancelled，保持 cancelled 状态
            if task_record.state != TaskStatus.CANCELLED.value:
                task_record.state = final_state.get("status", "failed")
            
            task_record.retry_count = final_state.get("retry_count", 0)
            
            # 提取可能存在的历史记录或草稿并合并
            try:
                history_data = json.loads(task_record.history) if task_record.history else {}
            except Exception:
                history_data = {}
            history_data.update({
                "draft_solution": final_state.get("draft_solution"),
                "review_decision": final_state.get("review_decision"),
                "review_feedback": final_state.get("review_feedback"),
            })
            task_record.history = json.dumps(history_data, ensure_ascii=False)
            
            task_record.final_result = final_state.get("final_result")
            task_record.token_usage = json.dumps({"total_tokens": final_state.get("total_tokens", 0)})
            task_record.error_code = final_state.get("error_msg")
            
            db.commit()
            print(f"[{task_id}] Workflow finished with status: {task_record.state}")
            
        except Exception as e:
            # 异常时进行防断保护
            print(f"[{task_id}] Workflow crashed: {e}")
            task_events.close(task_id)
            task_record = db.query(Task).filter(Task.task_id == task_id).first()
            if task_record:
                task_record.state = TaskStatus.FAILED.value
                task_record.error_code = f"System Error: {str(e)}"
                db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="智能题目解析 Agent 自动化流水线 API",
    version="1.0.0",
    lifespan=lifespan
)

# 配置 CORS，增加对大请求体（Base64图片）的支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 增大 FastAPI 接收 JSON 的默认限制（如果不加这个，太大的 Base64 会报 413 Payload Too Large）
# 改用纯 ASGI 中间件实现，避免 BaseHTTPMiddleware 阻塞 BackgroundTasks
class LimitUploadSizeASGI:
    def __init__(self, app, max_upload_size: int = 50 * 1024 * 1024): # 默认 50MB
        self.app = app
        self.max_upload_size = max_upload_size

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
            
        if scope["method"] == "POST":
            # 尝试从 headers 获取 content-length
            content_length = 0
            for name, value in scope.get("headers", []):
                if name.lower() == b"content-length":
                    try:
                        content_length = int(value)
                    except ValueError:
                        pass
                    break
            
            if content_length > self.max_upload_size:
                # 返回 413 Payload Too Large
                response = {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                    ],
                }
                await send(response)
                await send({
                    "type": "http.response.body",
                    "body": b'{"detail": "Payload too large"}',
                })
                return
                
        await self.app(scope, receive, send)

app.add_middleware(LimitUploadSizeASGI)

@app.post("/api/tasks", response_model=TaskCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_task(req: TaskCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    接收前端上传的题目图片地址（或 Base64），初始化一个解析任务并丢入后台队列执行。
    """
    try:
        new_task_id = f"task_{uuid.uuid4().hex[:8]}"
        new_thread_id = f"thread_{uuid.uuid4().hex[:8]}"
        
        new_task = Task(
            task_id=new_task_id,
            thread_id=new_thread_id,
            image_url=req.image_url,
            state=TaskStatus.QUEUED.value,
            history=json.dumps({
                "solver_config": req.solver_config.model_dump() if req.solver_config else {},
                "reviewer_config": req.reviewer_config.model_dump() if req.reviewer_config else {},
                "formatter_config": req.formatter_config.model_dump() if req.formatter_config else {}
            }, ensure_ascii=False)
        )
        
        db.add(new_task)
        db.commit()
        db.refresh(new_task)
        
        # 触发后台异步任务，执行图状态机
        background_tasks.add_task(run_agent_workflow_async, new_task.task_id, db)
        
        return TaskCreateResponse(
            task_id=new_task.task_id,
            status=TaskStatus(new_task.state)
        )
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Failed to create task: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create task: {str(e)}"
        )

@app.get("/api/logs")
async def get_logs(task_id: str = None, db: Session = Depends(get_db)):
    """
    Returns: List of AgentLog
    Description: 供前端后台使用，通过此接口在管理界面展示模型的完整请求、响应明细及 Token 消耗等。
    """
    query = db.query(AgentLog)
    if task_id:
        query = query.filter(AgentLog.task_id == task_id)
    logs = query.order_by(AgentLog.created_at.asc()).all()
    return logs

@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    """
    根据 task_id 获取任务的完整详情
    """
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id {task_id} not found."
        )
    return task

@app.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str, db: Session = Depends(get_db)):
    """
    Returns: Server-Sent Events (SSE)
    Format: data: {"event": "on_chat_model_stream", "chunk": "...", "node": "solver"}
    Description: 包含模型的流式输出（含思考过程）。只负责监听全局总线，不负责执行图。
    """
    task_record = db.query(Task).filter(Task.task_id == task_id).first()
    if not task_record:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        # 如果任务已经处于终态，直接发送结束信号并退出，防止前端傻等
        if task_record.state in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.MANUAL.value, TaskStatus.CANCELLED.value]:
            yield f"data: {json.dumps({'event': 'end'})}\n\n"
            return

        q = task_events.subscribe(task_id)
        try:
            while True:
                data = await q.get()
                if data is None: # None 表示工作流结束
                    yield f"data: {json.dumps({'event': 'end'})}\n\n"
                    break
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if task_id in task_events.queues and q in task_events.queues[task_id]:
                task_events.queues[task_id].remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/tasks/{task_id}/manual")
def submit_manual_review(task_id: str, req: ManualSubmitRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    人工接管提交接口。允许管理员将 manual 或 failed 的任务重新推入工作流。
    """
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        
    if task.state not in [TaskStatus.MANUAL.value, TaskStatus.FAILED.value]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Task is in {task.state} state, cannot be manually processed.")
        
    if req.action == "fail":
        task.state = TaskStatus.FAILED.value
        task.error_code = "Manually marked as failed."
        db.commit()
        return {"status": "success", "message": "Task marked as failed."}
        
    # 如果 action 是 resume，根据 PRD，相当于直接赋予草稿并让它去 formatting
    # 这里我们简化逻辑：更新数据库里的草稿，并把状态强行置为 queued（或其他入口），重新排队
    task.state = TaskStatus.QUEUED.value
    
    # 将人工编辑的草稿注入到历史字段，供下一次执行时读取
    current_history = json.loads(task.history) if task.history else {}
    current_history["draft_solution"] = req.draft_solution
    task.history = json.dumps(current_history, ensure_ascii=False)
    
    db.commit()
    
    # 重新触发后台工作流
    background_tasks.add_task(run_agent_workflow_async, task.task_id, db)
    
    return {"status": "success", "message": "Task resumed and queued for formatting."}

@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, db: Session = Depends(get_db)):
    """
    外部干预接口：熔断/终止一个正在执行的任务
    """
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        
    if task.state in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Task is already in {task.state} state.")
        
    task.state = TaskStatus.CANCELLED.value
    task.error_code = "Manually cancelled."
    db.commit()
    
    return {"status": "success", "message": "Task marked as cancelled."}
