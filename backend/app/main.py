from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import uuid
import asyncio
import json

from app.core.database import engine, Base, get_db
from app.models.domain import Task
from app.models.schemas import (
    TaskCreateRequest, TaskCreateResponse, TaskDetailResponse, 
    TaskStatus, ManualSubmitRequest
)
from app.agent.graph import build_graph

# 全局并发信号量，控制同时进行的大模型推理任务数（根据 PRD 要求默认为 5）
MAX_CONCURRENT_TASKS = 5
task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

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
        initial_state = {
            "task_id": task_record.task_id,
            "image_url": task_record.image_url,
            "status": task_record.state,
            "retry_count": task_record.retry_count,
            "total_tokens": 0
        }
        
        # 3. 运行图引擎
        try:
            # invoke 是同步阻塞的，在真实的纯异步场景下，如果里面有强阻塞的 I/O（如 httpx 没用 async）
            # 应该用 asyncio.to_thread 包装。LangGraph 的 ainvoke 也是可选项。
            # 为了兼容当前基于同步的 ChatOpenAI invoke，我们先用 to_thread 包装以释放事件循环。
            final_state = await asyncio.to_thread(graph_app.invoke, initial_state)
            
            # 4. 工作流结束，将最终状态落库
            task_record.state = final_state.get("status", "failed")
            task_record.retry_count = final_state.get("retry_count", 0)
            
            # 提取可能存在的历史记录或草稿
            history_data = {
                "draft_solution": final_state.get("draft_solution"),
                "review_decision": final_state.get("review_decision"),
                "review_feedback": final_state.get("review_feedback"),
            }
            task_record.history = json.dumps(history_data, ensure_ascii=False)
            
            task_record.final_result = final_state.get("final_result")
            task_record.token_usage = json.dumps({"total_tokens": final_state.get("total_tokens", 0)})
            task_record.error_code = final_state.get("error_msg")
            
            db.commit()
            print(f"[{task_id}] Workflow finished with status: {task_record.state}")
            
        except Exception as e:
            # 异常时进行防断保护
            print(f"[{task_id}] Workflow crashed: {e}")
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

# 配置 CORS，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 在生产环境中应该指定具体的前端地址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/tasks", response_model=TaskCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_task(req: TaskCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    接收前端上传的题目图片地址，初始化一个解析任务并丢入后台队列执行。
    """
    try:
        new_task_id = f"task_{uuid.uuid4().hex[:8]}"
        new_thread_id = f"thread_{uuid.uuid4().hex[:8]}"
        
        new_task = Task(
            task_id=new_task_id,
            thread_id=new_thread_id,
            image_url=req.image_url,
            state=TaskStatus.QUEUED.value
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create task: {str(e)}"
        )

@app.get("/api/tasks/{task_id}", response_model=TaskDetailResponse)
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
    # 更严谨的做法是在 initial_state 中带入修改过的 draft 并直接跳转到 formatting 节点
    task.state = TaskStatus.QUEUED.value
    
    # 将人工编辑的草稿注入到历史字段，供下一次执行时读取（简单实现）
    current_history = json.loads(task.history) if task.history else {}
    current_history["draft_solution"] = req.draft_solution
    task.history = json.dumps(current_history, ensure_ascii=False)
    
    db.commit()
    
    # 重新触发后台工作流（可以在 run_agent_workflow_async 内部解析历史来做断点恢复）
    background_tasks.add_task(run_agent_workflow_async, task.task_id, db)
    
    return {"status": "success", "message": "Task resumed and queued for formatting."}
