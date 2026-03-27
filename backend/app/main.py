from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    status,
    BackgroundTasks,
    Query,
)
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import uuid
import asyncio
import json
import os
import re
import io
import zipfile
import xml.etree.ElementTree as ET
import subprocess
import tempfile
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

from app.core.database import engine, Base, get_db, SessionLocal
from app.models.domain import Task, AgentLog
from app.models.schemas import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
    TaskStatus,
    ManualSubmitRequest,
    AdminTaskListResponse,
    AdminTaskUpdateRequest,
    AdminTaskUpdateResponse,
    AdminExportRequest,
    AdminLogListResponse,
    AdminLogItemResponse,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdateRequest,
    PromptTemplateItemResponse,
    PromptTemplateDetailResponse,
    PromptTemplatePayload,
    PromptTemplateCreateRequest,
    PaperBuilderDraftPayload,
    PaperBuilderDraftResponse,
    PaperBuilderDraftListResponse,
)
from app.agent.graph import build_graph
from app.agent.nodes.llm_client import coerce_token_count
from app.services.runtime_config import (
    create_template_from,
    get_template,
    list_templates,
    read_runtime_settings,
    update_runtime_settings,
    upsert_template,
)

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

VALID_RESUME_NODES = {"solver", "reviewer", "formatter"}
WORKFLOW_ORDER = ["solver", "reviewer", "formatter"]
graph_apps = {node: build_graph(node) for node in VALID_RESUME_NODES}
PAPER_BUILDER_DRAFTS_PATH = os.path.join(
    os.path.dirname(__file__), "config", "paper_builder_drafts.json"
)


def load_paper_builder_drafts_store() -> dict:
    if not os.path.exists(PAPER_BUILDER_DRAFTS_PATH):
        return {"drafts": {}}
    try:
        with open(PAPER_BUILDER_DRAFTS_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
            if not isinstance(data, dict):
                return {"drafts": {}}
            drafts = data.get("drafts")
            if not isinstance(drafts, dict):
                return {"drafts": {}}
            return {"drafts": drafts}
    except Exception:
        return {"drafts": {}}


def save_paper_builder_drafts_store(store: dict) -> None:
    os.makedirs(os.path.dirname(PAPER_BUILDER_DRAFTS_PATH), exist_ok=True)
    with open(PAPER_BUILDER_DRAFTS_PATH, "w", encoding="utf-8") as file:
        json.dump(store, file, ensure_ascii=False, indent=2)


def normalize_paper_builder_group_item(raw_group: dict) -> dict | None:
    if not isinstance(raw_group, dict):
        return None
    group_id = str(raw_group.get("group_id") or "").strip()
    if not group_id:
        return None
    group_name = (
        str(raw_group.get("group_name") or "未命名题型").strip() or "未命名题型"
    )
    raw_task_ids = raw_group.get("task_ids")
    if not isinstance(raw_task_ids, list):
        raw_task_ids = []
    task_ids = normalize_task_ids([str(task_id) for task_id in raw_task_ids])
    return {
        "group_id": group_id,
        "group_name": group_name,
        "task_ids": task_ids,
    }


def normalize_paper_builder_draft_record(draft_id: str, raw_value: dict) -> dict:
    draft_name = str(raw_value.get("name") or "默认排版草稿").strip() or "默认排版草稿"
    paper_subject = str(raw_value.get("paper_subject") or "").strip()
    paper_title = str(raw_value.get("paper_title") or "").strip()
    raw_groups = raw_value.get("groups")
    groups: list[dict] = []
    if isinstance(raw_groups, list):
        for raw_group in raw_groups:
            normalized_group = normalize_paper_builder_group_item(raw_group)
            if normalized_group:
                groups.append(normalized_group)
    updated_at = raw_value.get("updated_at")
    if not isinstance(updated_at, str):
        updated_at = None
    return {
        "draft_id": draft_id,
        "name": draft_name,
        "paper_subject": paper_subject,
        "paper_title": paper_title,
        "groups": groups,
        "updated_at": updated_at,
    }


def normalize_target_nodes(raw_nodes) -> list[str]:
    if not isinstance(raw_nodes, list):
        return []
    normalized = []
    for node in WORKFLOW_ORDER:
        if node in raw_nodes and node not in normalized:
            normalized.append(node)
    return normalized


def normalize_image_urls(
    raw_image_urls, fallback_image_url: str | None = None
) -> list[str]:
    normalized: list[str] = []

    if isinstance(raw_image_urls, list):
        for raw_url in raw_image_urls:
            if isinstance(raw_url, str):
                cleaned = raw_url.strip()
                if cleaned and cleaned not in normalized:
                    normalized.append(cleaned)

    if isinstance(fallback_image_url, str):
        cleaned = fallback_image_url.strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)

    return normalized


def validate_contiguous_nodes(nodes: list[str]) -> bool:
    if not nodes:
        return False
    indices = sorted(WORKFLOW_ORDER.index(node) for node in nodes)
    return all(indices[i + 1] - indices[i] == 1 for i in range(len(indices) - 1))


async def run_agent_workflow_async(
    task_id: str,
    start_node: str = "solver",
    target_nodes: list[str] | None = None,
):
    """
    异步执行图引擎工作流，并持久化每一步的状态。
    加入信号量以控制并发，防止触发模型 API 的 Rate Limit。
    """
    async with task_semaphore:
        print(f"[{task_id}] Acquired semaphore. Starting workflow...")

        # 1. 查询数据库获取任务初始信息；使用短生命周期会话，避免连接长时间占用
        with SessionLocal() as db:
            task_record = db.query(Task).filter(Task.task_id == task_id).first()
            if not task_record:
                print(f"[{task_id}] Error: Task not found in DB.")
                return

            # 2. 构造图引擎的初始状态
            # 尝试从 history 中提取动态模型配置和可恢复数据
            agent_configs = {}
            history_data = {}
            try:
                if task_record.history:
                    history_data = json.loads(task_record.history)
                    agent_configs = {
                        "solver": history_data.get("solver_config", {}),
                        "reviewer": history_data.get("reviewer_config", {}),
                        "formatter": history_data.get("formatter_config", {}),
                    }
            except Exception:
                history_data = {}

            runtime_settings = read_runtime_settings()
            workflow_template_id = history_data.get(
                "workflow_template_id"
            ) or runtime_settings.get("active_template_id")

            token_usage = {}
            try:
                token_usage = (
                    json.loads(task_record.token_usage)
                    if task_record.token_usage
                    else {}
                )
            except Exception:
                token_usage = {}

            if start_node not in VALID_RESUME_NODES:
                start_node = "solver"
            effective_target_nodes = normalize_target_nodes(
                target_nodes
                if target_nodes is not None
                else history_data.get("target_nodes")
            )
            if start_node in {"reviewer", "formatter"} and not history_data.get(
                "draft_solution"
            ):
                start_node = "solver"

            if effective_target_nodes:
                try:
                    start_index = WORKFLOW_ORDER.index(start_node)
                    effective_target_nodes = [
                        node
                        for node in effective_target_nodes
                        if WORKFLOW_ORDER.index(node) >= start_index
                    ]
                except ValueError:
                    effective_target_nodes = []

            effective_image_urls = normalize_image_urls(
                history_data.get("image_urls"), task_record.image_url
            )

            initial_state = {
                "task_id": task_record.task_id,
                "image_url": effective_image_urls[0] if effective_image_urls else "",
                "image_urls": effective_image_urls,
                "status": task_record.state,
                "retry_count": task_record.retry_count,
                "draft_solution": history_data.get("draft_solution"),
                "review_decision": history_data.get("review_decision"),
                "review_feedback": history_data.get("review_feedback"),
                "total_tokens": coerce_token_count(token_usage.get("total_tokens"), 0),
                "agent_configs": agent_configs,
                "target_nodes": effective_target_nodes,
                "workflow_template_id": workflow_template_id,
            }
        graph_app = graph_apps[start_node]

        # 3. 运行图引擎
        try:
            config = {"configurable": {"thread_id": task_id}}
            async for event in graph_app.astream_events(
                initial_state, config=config, version="v2"
            ):
                if event["event"] == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    node = event.get("metadata", {}).get("langgraph_node", "unknown")
                    data = json.dumps(
                        {
                            "event": "on_chat_model_stream",
                            "chunk": chunk.content,
                            "node": node,
                        },
                        ensure_ascii=False,
                    )
                    task_events.publish(task_id, data)

            # 工作流执行完毕，获取最终状态
            state_tuple = graph_app.get_state(config)
            final_state = state_tuple.values
            task_events.close(task_id)

            # 4. 工作流结束，将最终状态落库；使用新的短生命周期会话
            with SessionLocal() as db:
                task_record = db.query(Task).filter(Task.task_id == task_id).first()
                if not task_record:
                    return
                previous_state = task_record.state
                final_status = final_state.get("status", "failed")
                review_decision = final_state.get("review_decision")
                retry_count_value = final_state.get(
                    "retry_count", task_record.retry_count
                )
                reached_review_failure_terminal = (
                    review_decision == "FAIL"
                    and coerce_token_count(retry_count_value, 0) >= 1
                )
                if final_status in {
                    TaskStatus.QUEUED.value,
                    TaskStatus.SOLVING.value,
                    TaskStatus.REVIEWING.value,
                    TaskStatus.FORMATTING.value,
                }:
                    final_status = (
                        TaskStatus.FAILED.value
                        if reached_review_failure_terminal
                        else TaskStatus.COMPLETED.value
                    )

                # 如果在执行期间被标记为 cancelled，保持 cancelled 状态
                if task_record.state != TaskStatus.CANCELLED.value:
                    task_record.state = final_status

                task_record.retry_count = final_state.get("retry_count", 0)

                # 提取可能存在的历史记录或草稿并合并
                try:
                    history_data = (
                        json.loads(task_record.history) if task_record.history else {}
                    )
                except Exception:
                    history_data = {}

                if final_state.get("draft_solution") is not None:
                    history_data["draft_solution"] = final_state.get("draft_solution")
                if final_state.get("review_decision") is not None:
                    history_data["review_decision"] = final_state.get("review_decision")
                if final_state.get("review_feedback") is not None:
                    history_data["review_feedback"] = final_state.get("review_feedback")
                if final_state.get("workflow_template_id") is not None:
                    history_data["workflow_template_id"] = final_state.get(
                        "workflow_template_id"
                    )
                final_image_urls = normalize_image_urls(
                    final_state.get("image_urls"), task_record.image_url
                )
                if final_image_urls:
                    history_data["image_urls"] = final_image_urls
                    task_record.image_url = final_image_urls[0]
                if effective_target_nodes:
                    history_data["target_nodes"] = effective_target_nodes
                else:
                    history_data.pop("target_nodes", None)

                failed_node = final_state.get("failed_node")
                if not failed_node and reached_review_failure_terminal:
                    failed_node = "reviewer"
                if (
                    not failed_node
                    and final_status == TaskStatus.FAILED.value
                    and previous_state in VALID_RESUME_NODES
                ):
                    failed_node = previous_state
                if (
                    final_status == TaskStatus.FAILED.value
                    and failed_node in VALID_RESUME_NODES
                ):
                    history_data["failed_node"] = failed_node
                else:
                    history_data.pop("failed_node", None)
                task_record.history = json.dumps(history_data, ensure_ascii=False)

                final_result = final_state.get("final_result")
                task_record.final_result = final_result
                question_part, answer_part, _ = split_question_and_answer(
                    final_result or ""
                )
                task_record.question_preview = question_part or None
                task_record.answer_preview = answer_part or None
                task_record.token_usage = json.dumps(
                    {
                        "total_tokens": coerce_token_count(
                            final_state.get("total_tokens"), 0
                        )
                    }
                )
                task_record.error_code = final_state.get("error_msg")

                db.commit()
                print(f"[{task_id}] Workflow finished with status: {task_record.state}")

        except Exception as e:
            # 异常时进行防断保护
            print(f"[{task_id}] Workflow crashed: {e}")
            task_events.close(task_id)
            with SessionLocal() as db:
                task_record = db.query(Task).filter(Task.task_id == task_id).first()
                if task_record:
                    failed_node = (
                        task_record.state
                        if task_record.state in VALID_RESUME_NODES
                        else "solver"
                    )
                    try:
                        history_data = (
                            json.loads(task_record.history)
                            if task_record.history
                            else {}
                        )
                    except Exception:
                        history_data = {}
                    history_data["failed_node"] = failed_node
                    task_record.history = json.dumps(history_data, ensure_ascii=False)
                    task_record.state = TaskStatus.FAILED.value
                    task_record.error_code = f"System Error: {str(e)}"
                    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_task_preview_columns()
    yield


app = FastAPI(
    title="智能题目解析 Agent 自动化流水线 API", version="1.0.0", lifespan=lifespan
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
    def __init__(self, app, max_upload_size: int = 50 * 1024 * 1024):  # 默认 50MB
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
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"detail": "Payload too large"}',
                    }
                )
                return

        await self.app(scope, receive, send)


app.add_middleware(LimitUploadSizeASGI)


@app.post(
    "/api/tasks", response_model=TaskCreateResponse, status_code=status.HTTP_201_CREATED
)
async def create_task(
    req: TaskCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    接收前端上传的题目图片地址（或 Base64），初始化一个解析任务并丢入后台队列执行。
    """
    try:
        new_task_id = f"task_{uuid.uuid4().hex[:8]}"
        new_thread_id = f"thread_{uuid.uuid4().hex[:8]}"
        runtime_settings = read_runtime_settings()
        workflow_template_id = req.workflow_template_id or runtime_settings.get(
            "active_template_id"
        )
        normalized_image_urls = normalize_image_urls(req.image_urls, req.image_url)
        if not normalized_image_urls:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="image_url 或 image_urls 至少提供一个有效值。",
            )
        resume_node = req.entry_point or "solver"
        normalized_nodes = normalize_target_nodes(req.target_nodes)

        if req.target_nodes is not None:
            if not normalized_nodes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="target_nodes is invalid or empty.",
                )
            if not validate_contiguous_nodes(normalized_nodes):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="target_nodes must be a contiguous workflow chain.",
                )
            if resume_node not in normalized_nodes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="entry_point must be included in target_nodes.",
                )
            if resume_node != normalized_nodes[0]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"entry_point must be the first node in target_nodes: {normalized_nodes[0]}.",
                )
            target_nodes = normalized_nodes
        elif resume_node in VALID_RESUME_NODES and resume_node != "solver":
            start_index = WORKFLOW_ORDER.index(resume_node)
            target_nodes = WORKFLOW_ORDER[start_index:]
        else:
            target_nodes = None

        if resume_node in {"reviewer", "formatter"} and not req.draft_solution:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="draft_solution is required when entry_point is reviewer or formatter.",
            )

        new_task = Task(
            task_id=new_task_id,
            thread_id=new_thread_id,
            image_url=normalized_image_urls[0],
            state=TaskStatus.QUEUED.value,
            history=json.dumps(
                {
                    "image_urls": normalized_image_urls,
                    "solver_config": (
                        req.solver_config.model_dump() if req.solver_config else {}
                    ),
                    "reviewer_config": (
                        req.reviewer_config.model_dump() if req.reviewer_config else {}
                    ),
                    "formatter_config": (
                        req.formatter_config.model_dump()
                        if req.formatter_config
                        else {}
                    ),
                    "workflow_template_id": workflow_template_id,
                    "draft_solution": req.draft_solution,
                },
                ensure_ascii=False,
            ),
        )

        db.add(new_task)
        db.commit()
        db.refresh(new_task)

        # 触发后台异步任务，执行图状态机
        background_tasks.add_task(
            run_agent_workflow_async,
            new_task.task_id,
            resume_node,
            target_nodes,
        )

        return TaskCreateResponse(
            task_id=new_task.task_id, status=TaskStatus(new_task.state)
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import traceback

        traceback.print_exc()
        print(f"❌ Failed to create task: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create task: {str(e)}",
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


@app.get("/api/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task(task_id: str, db: Session = Depends(get_db)):
    """
    根据 task_id 获取任务的完整详情
    """
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id {task_id} not found.",
        )
    return task


@app.get("/api/tasks/active", response_model=list[TaskDetailResponse])
def list_active_tasks(db: Session = Depends(get_db)):
    """
    返回所有未完成任务，供主页面同步显示。
    """
    active_tasks = (
        db.query(Task)
        .filter(Task.state != TaskStatus.COMPLETED.value)
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .all()
    )
    return active_tasks


@app.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str):
    """
    Returns: Server-Sent Events (SSE)
    Format: data: {"event": "on_chat_model_stream", "chunk": "...", "node": "solver"}
    Description: 包含模型的流式输出（含思考过程）。只负责监听全局总线，不负责执行图。
    """
    with SessionLocal() as db:
        task_record = db.query(Task).filter(Task.task_id == task_id).first()
    if not task_record:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        # 如果任务已经处于终态，直接发送结束信号并退出，防止前端傻等
        if task_record.state in [
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.MANUAL.value,
            TaskStatus.CANCELLED.value,
        ]:
            yield f"data: {json.dumps({'event': 'end'})}\n\n"
            return

        q = task_events.subscribe(task_id)
        try:
            while True:
                data = await q.get()
                if data is None:  # None 表示工作流结束
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
def submit_manual_review(
    task_id: str,
    req: ManualSubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    人工接管提交接口。允许管理员将 manual 或 failed 的任务重新推入工作流。
    """
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
        )

    if req.action == "custom_run":
        allowed_states = {
            TaskStatus.MANUAL.value,
            TaskStatus.FAILED.value,
            TaskStatus.COMPLETED.value,
            TaskStatus.CANCELLED.value,
        }
    else:
        allowed_states = {TaskStatus.MANUAL.value, TaskStatus.FAILED.value}

    if task.state not in allowed_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task is in {task.state} state, cannot be manually processed.",
        )

    if req.action == "fail":
        task.state = TaskStatus.FAILED.value
        task.error_code = "Manually marked as failed."
        db.commit()
        return {"status": "success", "message": "Task marked as failed."}

    # 如果 action 是 resume，按失败节点恢复执行
    task.state = TaskStatus.QUEUED.value
    task.error_code = None

    # 将人工编辑的草稿注入到历史字段，供下一次执行时读取
    try:
        current_history = json.loads(task.history) if task.history else {}
    except Exception:
        current_history = {}
    if req.draft_solution is not None:
        current_history["draft_solution"] = req.draft_solution
    if req.solver_config is not None:
        current_history["solver_config"] = req.solver_config.model_dump()
    if req.reviewer_config is not None:
        current_history["reviewer_config"] = req.reviewer_config.model_dump()
    if req.formatter_config is not None:
        current_history["formatter_config"] = req.formatter_config.model_dump()
    if req.workflow_template_id is not None:
        current_history["workflow_template_id"] = req.workflow_template_id

    target_nodes: list[str] | None = None
    if req.action == "skip_review":
        if not current_history.get("draft_solution"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Draft solution is required when skipping review.",
            )
        resume_node = "formatter"
        target_nodes = ["formatter"]
        current_history["failed_node"] = "reviewer"
    elif req.action == "custom_run":
        if not req.entry_point:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="entry_point is required when action=custom_run.",
            )
        normalized_nodes = normalize_target_nodes(req.target_nodes)
        if not normalized_nodes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_nodes is required when action=custom_run.",
            )
        if not validate_contiguous_nodes(normalized_nodes):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_nodes must be a contiguous workflow chain.",
            )
        if req.entry_point not in normalized_nodes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="entry_point must be included in target_nodes.",
            )

        expected_entry = normalized_nodes[0]
        if req.entry_point != expected_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"entry_point must be the first node in target_nodes: {expected_entry}.",
            )

        if req.entry_point in {"reviewer", "formatter"} and not current_history.get(
            "draft_solution"
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="draft_solution is required when entry_point is reviewer or formatter.",
            )

        resume_node = req.entry_point
        target_nodes = normalized_nodes
        current_history["target_nodes"] = normalized_nodes
    else:
        resume_node = current_history.get("failed_node", "solver")
        if resume_node not in VALID_RESUME_NODES:
            resume_node = "solver"
        if resume_node in {"reviewer", "formatter"} and not current_history.get(
            "draft_solution"
        ):
            resume_node = "solver"
        current_history.pop("target_nodes", None)
    task.history = json.dumps(current_history, ensure_ascii=False)

    db.commit()

    # 重新触发后台工作流
    background_tasks.add_task(
        run_agent_workflow_async, task.task_id, resume_node, target_nodes
    )

    return {"status": "success", "message": f"Task resumed from node: {resume_node}."}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, db: Session = Depends(get_db)):
    """
    外部干预接口：熔断/终止一个正在执行的任务
    """
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
        )

    if task.state in [
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task is already in {task.state} state.",
        )

    task.state = TaskStatus.CANCELLED.value
    task.error_code = "Manually cancelled."
    db.commit()

    return {"status": "success", "message": "Task marked as cancelled."}


@app.get("/api/settings/runtime", response_model=RuntimeSettingsResponse)
def get_runtime_settings():
    return read_runtime_settings()


@app.put("/api/settings/runtime", response_model=RuntimeSettingsResponse)
def put_runtime_settings(req: RuntimeSettingsUpdateRequest):
    payload = req.model_dump(exclude_none=True, by_alias=True)
    return update_runtime_settings(payload)


@app.get("/api/paper-builder/drafts", response_model=PaperBuilderDraftListResponse)
def list_paper_builder_drafts():
    store = load_paper_builder_drafts_store()
    drafts = store.get("drafts") or {}
    items: list[dict] = []
    for draft_id, raw_value in drafts.items():
        if not isinstance(raw_value, dict):
            continue
        items.append(normalize_paper_builder_draft_record(draft_id, raw_value))
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"items": items}


@app.get(
    "/api/paper-builder/drafts/{draft_id}",
    response_model=PaperBuilderDraftResponse,
)
def get_paper_builder_draft(draft_id: str):
    normalized_draft_id = draft_id.strip()
    if not normalized_draft_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="draft_id 不能为空。",
        )

    store = load_paper_builder_drafts_store()
    drafts = store.get("drafts") or {}
    raw_value = drafts.get(normalized_draft_id)
    if not isinstance(raw_value, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft {normalized_draft_id} not found.",
        )
    return normalize_paper_builder_draft_record(normalized_draft_id, raw_value)


@app.put(
    "/api/paper-builder/drafts/{draft_id}",
    response_model=PaperBuilderDraftResponse,
)
def put_paper_builder_draft(draft_id: str, req: PaperBuilderDraftPayload):
    normalized_draft_id = draft_id.strip()
    if not normalized_draft_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="draft_id 不能为空。",
        )

    payload = req.model_dump()
    normalized_groups: list[dict] = []
    for group in payload.get("groups") or []:
        normalized_group = normalize_paper_builder_group_item(group)
        if normalized_group:
            normalized_groups.append(normalized_group)

    from datetime import datetime, timezone

    updated_at = datetime.now(timezone.utc).isoformat()

    store = load_paper_builder_drafts_store()
    drafts = store.setdefault("drafts", {})
    drafts[normalized_draft_id] = {
        "name": (payload.get("name") or "默认排版草稿").strip() or "默认排版草稿",
        "paper_subject": str(payload.get("paper_subject") or "").strip(),
        "paper_title": str(payload.get("paper_title") or "").strip(),
        "groups": normalized_groups,
        "updated_at": updated_at,
    }
    save_paper_builder_drafts_store(store)

    return normalize_paper_builder_draft_record(
        normalized_draft_id, drafts[normalized_draft_id]
    )


@app.get("/api/templates", response_model=list[PromptTemplateItemResponse])
def get_templates():
    return list_templates()


@app.get("/api/templates/{template_id}", response_model=PromptTemplateDetailResponse)
def get_template_detail(template_id: str):
    template = get_template(template_id)
    if not template or template.get("template_id") != template_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template {template_id} not found.",
        )
    return template


@app.post("/api/templates", response_model=PromptTemplateDetailResponse)
def create_template(req: PromptTemplateCreateRequest):
    try:
        return create_template_from(
            source_template_id=req.source_template_id,
            new_template_id=req.template_id,
            name=req.name,
            description=req.description or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@app.put("/api/templates/{template_id}", response_model=PromptTemplateDetailResponse)
def update_template(template_id: str, req: PromptTemplatePayload):
    try:
        return upsert_template(template_id, req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@app.get("/api/admin/tasks", response_model=AdminTaskListResponse)
def admin_list_tasks(
    task_id: str | None = Query(default=None),
    state: TaskStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(Task)
    if task_id:
        query = query.filter(Task.task_id.like(f"%{task_id}%"))
    if state:
        query = query.filter(Task.state == state.value)

    total = query.count()
    items = (
        query.order_by(Task.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return AdminTaskListResponse(
        total=total, page=page, page_size=page_size, items=items
    )


def split_question_and_answer(final_result: str) -> tuple[str, str, bool]:
    text = (final_result or "").strip()
    if not text:
        return "", "", True

    answer_start = text.find("【正解】")
    if answer_start < 0:
        answer_start = text.find("【解析】")

    if answer_start < 0:
        return text, "", True

    question_part = text[:answer_start].strip()

    answer_end_marker = "【答案延伸】"
    answer_end = text.find(answer_end_marker, answer_start)
    if answer_end >= 0:
        answer_part = text[answer_start : answer_end + len(answer_end_marker)].strip()
    else:
        answer_part = text[answer_start:].strip()

    return question_part, answer_part, False


def strip_leading_numbering(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    return re.sub(r"^\s*[（(]?\d+[）).、．]\s*", "", cleaned)


def to_chinese_section_number(index: int) -> str:
    numerals = [
        "一",
        "二",
        "三",
        "四",
        "五",
        "六",
        "七",
        "八",
        "九",
        "十",
        "十一",
        "十二",
    ]
    if 1 <= index <= len(numerals):
        return numerals[index - 1]
    return str(index)


def normalize_task_ids(task_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen = set()
    for raw_task_id in task_ids:
        task_id = (raw_task_id or "").strip()
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        normalized.append(task_id)
    return normalized


def apply_docx_default_style(
    docx_bytes: bytes,
    paper_subject: str = "",
    paper_title: str = "",
) -> bytes:
    """
    强制统一 DOCX 默认样式：
    - 中文：宋体
    - 英文：Times New Roman
    - 字号：小四（12pt -> 24 half-points）
    """
    if not docx_bytes:
        return docx_bytes

    try:
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        ET.register_namespace("w", ns["w"])

        def qn(tag: str) -> str:
            return f"{{{ns['w']}}}{tag}"

        def ensure_child(parent: ET.Element, tag: str) -> ET.Element:
            child = parent.find(f"w:{tag}", ns)
            if child is None:
                child = ET.SubElement(parent, qn(tag))
            return child

        in_zip = io.BytesIO(docx_bytes)
        out_zip = io.BytesIO()

        answer_title = ""
        if paper_title:
            answer_title = f"{paper_title}参考答案"

        with zipfile.ZipFile(in_zip, "r") as zin, zipfile.ZipFile(
            out_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/styles.xml":
                    root = ET.fromstring(data)

                    # 1) docDefaults 下统一 run 默认字体与字号
                    doc_defaults = root.find("w:docDefaults", ns)
                    if doc_defaults is None:
                        doc_defaults = ET.SubElement(root, qn("docDefaults"))

                    rpr_default = ensure_child(doc_defaults, "rPrDefault")
                    rpr = ensure_child(rpr_default, "rPr")

                    rfonts = ensure_child(rpr, "rFonts")
                    rfonts.set(qn("ascii"), "Times New Roman")
                    rfonts.set(qn("hAnsi"), "Times New Roman")
                    rfonts.set(qn("eastAsia"), "宋体")
                    rfonts.set(qn("cs"), "Times New Roman")

                    sz = ensure_child(rpr, "sz")
                    sz.set(qn("val"), "24")  # 12pt
                    sz_cs = ensure_child(rpr, "szCs")
                    sz_cs.set(qn("val"), "24")

                    # 2) Normal 样式再覆盖一遍，减少阅读器差异
                    for style in root.findall("w:style", ns):
                        if style.get(qn("styleId")) == "Normal":
                            style_rpr = ensure_child(style, "rPr")
                            style_rfonts = ensure_child(style_rpr, "rFonts")
                            style_rfonts.set(qn("ascii"), "Times New Roman")
                            style_rfonts.set(qn("hAnsi"), "Times New Roman")
                            style_rfonts.set(qn("eastAsia"), "宋体")
                            style_rfonts.set(qn("cs"), "Times New Roman")

                            style_sz = ensure_child(style_rpr, "sz")
                            style_sz.set(qn("val"), "24")
                            style_sz_cs = ensure_child(style_rpr, "szCs")
                            style_sz_cs.set(qn("val"), "24")
                            break

                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)

                if item.filename == "word/document.xml":
                    root = ET.fromstring(data)

                    def paragraph_text(paragraph: ET.Element) -> str:
                        texts: list[str] = []
                        for t in paragraph.findall(".//w:t", ns):
                            if t.text:
                                texts.append(t.text)
                        return "".join(texts).strip()

                    def apply_paragraph_title_style(
                        paragraph: ET.Element, size_val: str
                    ):
                        ppr = paragraph.find("w:pPr", ns)
                        if ppr is None:
                            ppr = ET.SubElement(paragraph, qn("pPr"))
                        jc = ppr.find("w:jc", ns)
                        if jc is None:
                            jc = ET.SubElement(ppr, qn("jc"))
                        jc.set(qn("val"), "center")

                        runs = paragraph.findall("w:r", ns)
                        for run in runs:
                            rpr = run.find("w:rPr", ns)
                            if rpr is None:
                                rpr = ET.SubElement(run, qn("rPr"))

                            rfonts = rpr.find("w:rFonts", ns)
                            if rfonts is None:
                                rfonts = ET.SubElement(rpr, qn("rFonts"))
                            rfonts.set(qn("ascii"), "Times New Roman")
                            rfonts.set(qn("hAnsi"), "Times New Roman")
                            rfonts.set(qn("eastAsia"), "宋体")
                            rfonts.set(qn("cs"), "Times New Roman")

                            sz = rpr.find("w:sz", ns)
                            if sz is None:
                                sz = ET.SubElement(rpr, qn("sz"))
                            sz.set(qn("val"), size_val)

                            sz_cs = rpr.find("w:szCs", ns)
                            if sz_cs is None:
                                sz_cs = ET.SubElement(rpr, qn("szCs"))
                            sz_cs.set(qn("val"), size_val)

                            bold = rpr.find("w:b", ns)
                            if bold is None:
                                bold = ET.SubElement(rpr, qn("b"))
                            bold_cs = rpr.find("w:bCs", ns)
                            if bold_cs is None:
                                bold_cs = ET.SubElement(rpr, qn("bCs"))

                    subject_targets = {paper_subject} if paper_subject else set()
                    title_targets = {paper_title, answer_title}
                    title_targets = {text for text in title_targets if text}

                    for paragraph in root.findall(".//w:p", ns):
                        text = paragraph_text(paragraph)
                        if not text:
                            continue
                        if text in subject_targets:
                            # 20pt = 40 half-points
                            apply_paragraph_title_style(paragraph, "40")
                        elif text in title_targets:
                            # 小二 18pt = 36 half-points
                            apply_paragraph_title_style(paragraph, "36")

                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)

                zout.writestr(item, data)

        return out_zip.getvalue()
    except Exception:
        # 样式后处理失败时回退原始 docx，不中断导出
        return docx_bytes


def normalize_export_block_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    # 若模型输出了“【解析】后直接跟正文”，导出时强制换行，避免阅读拥挤。
    normalized = re.sub(r"【解析】\s*(?=\S)", "【解析】\n", normalized)
    return normalized


def append_numbered_plain_block(
    lines: list[str],
    index: int,
    content: str,
    fallback: str,
) -> None:
    block = normalize_export_block_text(content)
    if not block:
        lines.append(f"{index}、{fallback}")
        lines.append("")
        return

    block_lines = block.split("\n")
    first_line = block_lines[0].strip()
    lines.append(f"{index}、{first_line}")
    for line in block_lines[1:]:
        lines.append(line)
    lines.append("")


def build_split_export_markdown(
    groups: list[dict],
    paper_subject: str = "",
    paper_title: str = "",
) -> str:
    subject = (paper_subject or "").strip()
    title = (paper_title or "").strip()
    answer_title = f"{title}参考答案" if title else ""

    lines: list[str] = []
    if subject:
        lines.append(subject)
        lines.append("")
    if title:
        lines.append(title)
        lines.append("")

    for group_index, group in enumerate(groups, start=1):
        items = group.get("items") or []
        if not items:
            continue
        group_name = (group.get("group_name") or "未命名题型").strip()
        section_no = to_chinese_section_number(group_index)
        lines.append(f"{section_no}、{group_name}")
        lines.append("")
        for item_index, item in enumerate(items, start=1):
            question_text = strip_leading_numbering(item.get("question") or "")
            append_numbered_plain_block(
                lines,
                item_index,
                question_text,
                "（题目内容为空）",
            )

    if answer_title:
        lines.append(answer_title)
        lines.append("")
    for group_index, group in enumerate(groups, start=1):
        items = group.get("items") or []
        if not items:
            continue
        group_name = (group.get("group_name") or "未命名题型").strip()
        section_no = to_chinese_section_number(group_index)
        lines.append(f"{section_no}、{group_name}")
        lines.append("")
        for item_index, item in enumerate(items, start=1):
            answer_text = strip_leading_numbering(item.get("answer") or "")
            if answer_text:
                content = answer_text
            elif item.get("only_question"):
                content = "（仅题目，未识别到【正解】或【解析】答案段）"
            else:
                content = "（答案内容为空）"
            append_numbered_plain_block(lines, item_index, content, "（答案内容为空）")

    return "\n".join(lines).strip() + "\n"


def collect_export_items(
    task_ids: list[str], db: Session
) -> list[tuple[str, str, str, bool]]:
    normalized_task_ids = normalize_task_ids(task_ids)
    tasks = db.query(Task).filter(Task.task_id.in_(normalized_task_ids)).all()
    task_map = {task.task_id: task for task in tasks}

    items_with_result: list[tuple[str, str, str, bool]] = []
    for task_id in normalized_task_ids:
        task = task_map.get(task_id)
        if not task:
            continue
        question_part = (task.question_preview or "").strip()
        answer_part = (task.answer_preview or "").strip()

        # 兼容旧数据：若历史任务未持久化拆分字段，则回退到 final_result 现场切分。
        if not question_part and not answer_part:
            final_result = (task.final_result or "").strip()
            if not final_result:
                continue
            question_part, answer_part, only_question = split_question_and_answer(
                final_result
            )
        else:
            only_question = not bool(answer_part)

        items_with_result.append((task_id, question_part, answer_part, only_question))
    return items_with_result


def collect_grouped_export_items(
    req: AdminExportRequest,
    db: Session,
) -> list[dict]:
    # 新版分组导出优先
    if req.groups:
        grouped_items: list[dict] = []
        for index, group in enumerate(req.groups, start=1):
            group_name = (group.group_name or "").strip() or f"题型{index}"
            items = collect_export_items(group.task_ids, db)
            if not items:
                continue
            grouped_items.append(
                {
                    "group_id": group.group_id or f"group-{index}",
                    "group_name": group_name,
                    "items": [
                        {
                            "task_id": task_id,
                            "question": question_part,
                            "answer": answer_part,
                            "only_question": only_question,
                        }
                        for task_id, question_part, answer_part, only_question in items
                    ],
                }
            )
        return grouped_items

    # 旧版兼容：扁平 task_ids 自动封装为单分组
    fallback_items = collect_export_items(req.task_ids, db)
    if not fallback_items:
        return []
    return [
        {
            "group_id": "legacy-group",
            "group_name": "题目",
            "items": [
                {
                    "task_id": task_id,
                    "question": question_part,
                    "answer": answer_part,
                    "only_question": only_question,
                }
                for task_id, question_part, answer_part, only_question in fallback_items
            ],
        }
    ]


@app.post("/api/admin/tasks/export/md")
def admin_export_tasks_md(req: AdminExportRequest, db: Session = Depends(get_db)):
    has_groups = bool(req.groups)
    task_ids = normalize_task_ids(req.task_ids)
    if not has_groups and not task_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="task_ids 或 groups 至少提供一个。",
        )

    grouped_items = collect_grouped_export_items(req, db)
    if not grouped_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="所选任务没有可导出的最终排版结果。",
        )

    markdown_content = build_split_export_markdown(
        grouped_items,
        req.paper_subject or "",
        req.paper_title or "",
    )
    filename = f"final_results_{uuid.uuid4().hex[:8]}.md"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        iter([markdown_content.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers=headers,
    )


@app.post("/api/admin/tasks/export/docx")
def admin_export_tasks_docx(req: AdminExportRequest, db: Session = Depends(get_db)):
    has_groups = bool(req.groups)
    task_ids = normalize_task_ids(req.task_ids)
    if not has_groups and not task_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="task_ids 或 groups 至少提供一个。",
        )

    grouped_items = collect_grouped_export_items(req, db)

    if not grouped_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="所选任务没有可导出的最终排版结果。",
        )

    markdown_content = build_split_export_markdown(
        grouped_items,
        req.paper_subject or "",
        req.paper_title or "",
    )

    try:
        with tempfile.TemporaryDirectory(prefix="task_export_") as tmp_dir:
            md_path = os.path.join(tmp_dir, "final_results.md")
            docx_path = os.path.join(tmp_dir, "final_results.docx")

            with open(md_path, "w", encoding="utf-8") as md_file:
                md_file.write(markdown_content)

            result = subprocess.run(
                [
                    "pandoc",
                    "-f",
                    "markdown+hard_line_breaks",
                    md_path,
                    "-o",
                    docx_path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                last_error_text = (result.stderr or result.stdout or "").strip()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "DOCX 导出失败，请确认已安装 pandoc。"
                        + (f" 详情: {last_error_text}" if last_error_text else "")
                    ),
                )

            if not os.path.exists(docx_path):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="DOCX 文件生成失败。",
                )

            with open(docx_path, "rb") as docx_file:
                docx_bytes = docx_file.read()

            # 强制统一导出 DOCX 的字体与字号
            docx_bytes = apply_docx_default_style(
                docx_bytes,
                req.paper_subject or "",
                req.paper_title or "",
            )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DOCX 导出失败，未找到 pandoc 命令。",
        )

    filename = f"final_results_{uuid.uuid4().hex[:8]}.docx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        iter([docx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )


@app.get("/api/admin/tasks/{task_id}", response_model=TaskDetailResponse)
def admin_get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
        )
    return task


@app.patch("/api/admin/tasks/{task_id}", response_model=AdminTaskUpdateResponse)
def admin_update_task(
    task_id: str, req: AdminTaskUpdateRequest, db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
        )

    updates = req.model_dump(exclude_unset=True)
    for field_name, value in updates.items():
        if field_name == "state" and value is not None:
            setattr(task, field_name, value.value)
        else:
            setattr(task, field_name, value)

    if "final_result" in updates:
        question_part, answer_part, _ = split_question_and_answer(
            updates.get("final_result") or ""
        )
        task.question_preview = question_part or None
        task.answer_preview = answer_part or None

    db.commit()
    db.refresh(task)
    return AdminTaskUpdateResponse(message="Task updated successfully.", task=task)


@app.delete("/api/admin/tasks/{task_id}")
def admin_delete_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
        )

    db.query(AgentLog).filter(AgentLog.task_id == task_id).delete()
    db.delete(task)
    db.commit()
    return {"status": "success", "message": f"Task {task_id} deleted."}


def ensure_task_preview_columns() -> None:
    """为已有 SQLite 数据库补齐新列，避免老库缺列导致查询失败。"""
    try:
        with engine.begin() as conn:
            columns = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()
            }
            if "question_preview" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN question_preview TEXT"))
            if "answer_preview" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN answer_preview TEXT"))
    except Exception as exc:
        print(f"[DB] Failed to ensure preview columns: {exc}")


@app.get("/api/admin/logs", response_model=AdminLogListResponse)
def admin_list_logs(task_id: str, db: Session = Depends(get_db)):
    logs = (
        db.query(AgentLog)
        .filter(AgentLog.task_id == task_id)
        .order_by(AgentLog.created_at.asc())
        .all()
    )
    return AdminLogListResponse(
        total=len(logs),
        items=[AdminLogItemResponse.model_validate(log) for log in logs],
    )
