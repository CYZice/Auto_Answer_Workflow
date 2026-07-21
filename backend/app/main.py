from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    status,
    BackgroundTasks,
    Query,
)
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_, text
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
import shutil
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

from app.core.database import engine, Base, get_db, SessionLocal
from app.models.domain import Task, AgentLog, TaskArtifact, TargetSystemDeliveryLock, TargetSystemTask, ErrataItem, ErrataJob
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
    TaskInputUpdateRequest,
    TaskRunRequest,
    TaskArtifactResponse,
    TaskOperationRequest,
)
from app.agent.graph import build_errata_graph, build_graph
from app.agent.nodes.reviewer import review_node
from app.agent.nodes.llm_client import coerce_token_count
from app.services.runtime_config import (
    create_template_from,
    get_template,
    list_templates,
    read_model_defaults,
    read_public_runtime_settings,
    read_runtime_settings,
    update_runtime_settings,
    upsert_template,
    validate_errata_workflow_prompts,
)
from app.api.mineru_routes import router as mineru_router
from app.api.errata_routes import router as errata_router
from app.api.paper_routes import router as paper_router
from app.api.target_system_routes import router as target_system_router
from app.services.task_artifacts import latest_task_artifact
from app.services.mineru_jobs import resume_pending_mineru_jobs
from app.services.errata_service import (
    _ensure_errata_task_column,
    ensure_errata_tasks,
    errata_solver_node,
    errata_formatter_node,
    errata_format_node,
    errata_review_node,
    migrate_errata_workflow_v2,
)

# 全局并发信号量，控制同时进行的大模型推理任务数
MAX_CONCURRENT_TASKS = 10
task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# 全局流式事件总线，用于将后台工作流的流式输出分发给所有 SSE 客户端
from app.core.events import task_events

STANDARD_NODE_ORDER = ["solver", "reviewer", "formatter"]
ERRATA_NODE_ORDER = [
    "solver",
    "reviewer",
    "formatter",
    "errata_adjudication",
    "word_composition",
]
VALID_RESUME_NODES = set(STANDARD_NODE_ORDER) | set(ERRATA_NODE_ORDER)
WORKFLOW_ORDER = STANDARD_NODE_ORDER
graph_apps = {
    ("standard", node): build_graph(node) for node in STANDARD_NODE_ORDER
}


def get_graph_app(workflow_type: str, start_node: str):
    graph_kind = "errata" if workflow_type == "errata" else "standard"
    key = (graph_kind, start_node)
    if key not in graph_apps:
        graph_apps[key] = build_errata_graph(
            start_node,
            nodes={
                "solver": errata_solver_node,
                "reviewer": review_node,
                "formatter": errata_formatter_node,
                "errata_adjudication": errata_review_node,
                "word_composition": errata_format_node,
            },
        )
    return graph_apps[key]
PAPER_BUILDER_DRAFTS_PATH = os.path.join(
    os.path.dirname(__file__), "config", "paper_builder_drafts.json"
)
LEGACY_DB_PATH = Path("/app/agent_tasks.db")
LEGACY_DB_WAL_PATH = Path("/app/agent_tasks.db-wal")
LEGACY_DB_SHM_PATH = Path("/app/agent_tasks.db-shm")


def migrate_legacy_sqlite_db() -> None:
    current_db_url = str(engine.url)
    if not current_db_url.startswith("sqlite:///"):
        return

    target_db_path = Path(current_db_url.replace("sqlite:///", "", 1))
    target_db_path.parent.mkdir(parents=True, exist_ok=True)

    if target_db_path.exists() or not LEGACY_DB_PATH.exists():
        return

    logger_text = f"[DB] Migrating legacy SQLite database from {LEGACY_DB_PATH} to {target_db_path}"
    print(logger_text)
    shutil.copy2(LEGACY_DB_PATH, target_db_path)

    wal_target = Path(f"{target_db_path}-wal")
    shm_target = Path(f"{target_db_path}-shm")
    if LEGACY_DB_WAL_PATH.exists():
        shutil.copy2(LEGACY_DB_WAL_PATH, wal_target)
    if LEGACY_DB_SHM_PATH.exists():
        shutil.copy2(LEGACY_DB_SHM_PATH, shm_target)


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


def workflow_node_order(workflow_type: str) -> list[str]:
    return ERRATA_NODE_ORDER if workflow_type == "errata" else STANDARD_NODE_ORDER


def normalize_target_nodes(raw_nodes, workflow_type: str = "standard") -> list[str]:
    if not isinstance(raw_nodes, list):
        return []
    normalized = []
    for node in workflow_node_order(workflow_type):
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


def validate_ordered_target_nodes(nodes: list[str], workflow_type: str = "standard") -> bool:
    if not nodes:
        return False
    if len(set(nodes)) != len(nodes):
        return False
    try:
        indices = [workflow_node_order(workflow_type).index(node) for node in nodes]
    except ValueError:
        return False
    return all(indices[i] < indices[i + 1] for i in range(len(indices) - 1))


def validate_requested_target_nodes(raw_nodes, workflow_type: str = "standard") -> list[str]:
    if not isinstance(raw_nodes, list):
        return []

    requested_nodes: list[str] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, str):
            return []
        cleaned = raw_node.strip()
        if not cleaned:
            return []
        requested_nodes.append(cleaned)

    if not validate_ordered_target_nodes(requested_nodes, workflow_type):
        return []
    return requested_nodes


def requires_draft_for_entry_point(entry_point: str, draft_solution: str | None) -> bool:
    return entry_point in {"reviewer", "formatter", "errata_adjudication", "word_composition"} and not draft_solution


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
            workflow_input_revision = int(task_record.input_revision or 1)
            workflow_type = task_record.workflow_type or "standard"
            node_order = workflow_node_order(workflow_type)

            # 2. 构造图引擎的初始状态。模型配置只从服务端全局配置读取。
            model_defaults = read_model_defaults()
            agent_configs = {
                "solver": model_defaults.get("solver_config") or {},
                "reviewer": model_defaults.get("reviewer_config") or {},
                "formatter": model_defaults.get("formatter_config") or {},
            }
            history_data = {}
            try:
                if task_record.history:
                    history_data = json.loads(task_record.history)
            except Exception as e:
                print(f"[DEBUG main.py] Failed to parse history: {e}")
                history_data = {}

            if start_node in {"reviewer", "formatter"} and not history_data.get("draft_solution"):
                artifact = latest_task_artifact(
                    task_id,
                    "solver",
                    int(task_record.input_revision or 1),
                )
                if artifact:
                    history_data["draft_solution"] = artifact.content
            if workflow_type == "errata" and start_node in {"errata_adjudication", "word_composition"}:
                artifact = latest_task_artifact(
                    task_id, "formatter", int(task_record.input_revision or 1)
                )
                if artifact:
                    history_data["formatted_solution"] = artifact.content
            if workflow_type == "errata" and start_node == "word_composition":
                artifact = latest_task_artifact(
                    task_id, "errata_adjudication", int(task_record.input_revision or 1)
                )
                if artifact:
                    try:
                        history_data["errata_decision"] = json.loads(artifact.content)
                    except json.JSONDecodeError:
                        history_data.pop("errata_decision", None)

            runtime_settings = read_runtime_settings()
            workflow_template_id = (
                "errata_workflow"
                if workflow_type == "errata"
                else runtime_settings.get("active_template_id")
            )

            token_usage = {}
            try:
                token_usage = (
                    json.loads(task_record.token_usage)
                    if task_record.token_usage
                    else {}
                )
            except Exception:
                token_usage = {}

            if start_node not in node_order:
                start_node = node_order[0]
            effective_target_nodes = normalize_target_nodes(
                target_nodes
                if target_nodes is not None
                else history_data.get("target_nodes"),
                workflow_type,
            )
            if start_node in {"reviewer", "formatter"} and not history_data.get(
                "draft_solution"
            ):
                start_node = node_order[0]

            if effective_target_nodes:
                try:
                    start_index = node_order.index(start_node)
                    effective_target_nodes = [
                        node
                        for node in effective_target_nodes
                        if node_order.index(node) >= start_index
                    ]
                except ValueError:
                    effective_target_nodes = []

            try:
                explicit_image_urls = json.loads(task_record.image_urls_json or "[]")
            except Exception:
                explicit_image_urls = []
            effective_image_urls = normalize_image_urls(
                explicit_image_urls or history_data.get("image_urls"), task_record.image_url
            )

            initial_state = {
                "task_id": task_record.task_id,
                "input_revision": workflow_input_revision,
                "image_url": effective_image_urls[0] if effective_image_urls else "",
                "image_urls": effective_image_urls,
                "status": task_record.state,
                "retry_count": task_record.retry_count,
                "draft_solution": history_data.get("draft_solution"),
                "formatted_solution": history_data.get("formatted_solution"),
                "errata_decision": history_data.get("errata_decision"),
                "review_decision": history_data.get("review_decision"),
                "review_feedback": history_data.get("review_feedback"),
                "total_tokens": coerce_token_count(token_usage.get("total_tokens"), 0),
                "agent_configs": agent_configs,
                "target_nodes": effective_target_nodes,
                "workflow_template_id": workflow_template_id,
                "question_text": task_record.question_text or history_data.get("question_text")
                or history_data.get("question_content"),
                "workflow_type": workflow_type,
                "source_id": task_record.source_id,
                "source_item_id": task_record.source_item_id,
            }
        graph_app = get_graph_app(workflow_type, start_node)

        # 3. 运行图引擎
        try:
            config = {"configurable": {"thread_id": task_id}}
            last_announced_node = None
            async for event in graph_app.astream_events(
                initial_state, config=config, version="v2"
            ):
                node = event.get("metadata", {}).get("langgraph_node")
                if node in workflow_node_order(workflow_type) and node != last_announced_node:
                    task_events.publish(
                        task_id,
                        json.dumps(
                            {"event": "node_start", "node": node},
                            ensure_ascii=False,
                        ),
                    )
                    last_announced_node = node
                if event["event"] == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    data = json.dumps(
                        {
                            "event": "on_chat_model_stream",
                            "chunk": chunk.content,
                            "node": node or "unknown",
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
                if int(task_record.input_revision or 1) != workflow_input_revision:
                    task_record.state = TaskStatus.MANUAL.value
                    task_record.error_code = "input_changed_during_run"
                    db.commit()
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
                    if reached_review_failure_terminal:
                        final_status = TaskStatus.FAILED.value
                    elif effective_target_nodes and node_order[-1] not in effective_target_nodes:
                        final_status = TaskStatus.MANUAL.value
                    else:
                        final_status = TaskStatus.COMPLETED.value

                # 如果在执行期间被人工暂停/终止，保持人工状态。
                if task_record.state not in {
                    TaskStatus.CANCELLED.value,
                    TaskStatus.PAUSED.value,
                    TaskStatus.TERMINATED.value,
                    TaskStatus.ABANDONED.value,
                }:
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
                if final_state.get("formatted_solution") is not None:
                    history_data["formatted_solution"] = final_state.get("formatted_solution")
                if final_state.get("errata_decision") is not None:
                    history_data["errata_decision"] = final_state.get("errata_decision")
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
                if final_image_urls and workflow_type != "errata":
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
                    and task_record.current_node in node_order
                ):
                    failed_node = task_record.current_node
                if (
                    final_status == TaskStatus.FAILED.value
                    and failed_node in node_order
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

                _sync_target_system_workflow_state(db, task_record)

                db.commit()
                print(f"[{task_id}] Workflow finished with status: {task_record.state}")

        except Exception as e:
            # 异常时进行防断保护
            print(f"[{task_id}] Workflow crashed: {e}")
            task_events.close(task_id)
            with SessionLocal() as db:
                task_record = db.query(Task).filter(Task.task_id == task_id).first()
                if task_record:
                    node_order = workflow_node_order(task_record.workflow_type or "standard")
                    failed_node = task_record.current_node if task_record.current_node in node_order else node_order[0]
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
                    _sync_target_system_workflow_state(db, task_record)
                    db.commit()


def _sync_target_system_workflow_state(db: Session, task_record: Task) -> None:
    """Keep the target-system review state aligned with its linked workflow."""
    if task_record.source_kind != "target_system":
        return

    target_item = db.query(TargetSystemTask).filter(
        TargetSystemTask.workflow_task_id == task_record.task_id
    ).first()
    if not target_item:
        return

    if task_record.state == TaskStatus.COMPLETED.value and (task_record.final_result or "").strip():
        target_item.status = "review_pending"
        target_item.error_message = None
    elif task_record.state in {
        TaskStatus.FAILED.value,
        TaskStatus.MANUAL.value,
        TaskStatus.PAUSED.value,
        TaskStatus.TERMINATED.value,
        TaskStatus.ABANDONED.value,
        TaskStatus.CANCELLED.value,
    }:
        target_item.error_message = task_record.error_code or f"解题工作流已{task_record.state}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_errata_workflow_prompts()
    migrate_legacy_sqlite_db()
    Base.metadata.create_all(bind=engine)
    ensure_task_preview_columns()
    _ensure_errata_task_column()
    migrate_task_workflow_metadata()
    migrate_errata_workflow_v2()
    ensure_target_system_columns()
    with SessionLocal() as db:
        db.query(Task).filter(Task.state.in_(["queued", "solving", "reviewing", "formatting"])).update(
            {Task.state: TaskStatus.PAUSED.value, Task.error_code: "服务重启后已暂停，请手动继续"},
            synchronize_session=False,
        )
        db.query(TargetSystemTask).filter(TargetSystemTask.status == "filling").update(
            {TargetSystemTask.status: "fill_failed", TargetSystemTask.error_message: "服务重启后等待手动重试"},
            synchronize_session=False,
        )
        lock = db.query(TargetSystemDeliveryLock).filter(TargetSystemDeliveryLock.id == 1).first()
        if not lock:
            lock = TargetSystemDeliveryLock(id=1)
            db.add(lock)
        if lock.target_task_id:
            active = db.query(TargetSystemTask).filter(TargetSystemTask.id == lock.target_task_id).first()
            if not active or active.status not in {"awaiting_user_submit", "fill_failed"}:
                lock.target_task_id = None
        db.commit()
    await resume_pending_mineru_jobs()
    yield


app = FastAPI(
    title="智能题目解析 Agent 自动化流水线 API", version="1.0.0", lifespan=lifespan
)
app.include_router(mineru_router)
app.include_router(errata_router)
app.include_router(paper_router)
app.include_router(target_system_router)

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
        model_defaults = read_model_defaults()
        workflow_template_id = (
            req.workflow_template_id
            or model_defaults.get("workflow_template_id")
            or runtime_settings.get("active_template_id")
        )
        normalized_image_urls = normalize_image_urls(req.image_urls, req.image_url)
        normalized_question_text = (req.question_text or "").strip()
        if not normalized_image_urls and not normalized_question_text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="image_url、image_urls、question_text 至少提供一个有效值。",
            )
        resume_node = req.entry_point or "solver"
        requested_nodes = validate_requested_target_nodes(req.target_nodes)

        if req.target_nodes is not None:
            if not requested_nodes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="target_nodes is invalid or empty.",
                )
            if resume_node not in requested_nodes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="entry_point must be included in target_nodes.",
                )
            if resume_node != requested_nodes[0]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"entry_point must be the first node in target_nodes: {requested_nodes[0]}.",
                )
            normalized_nodes = normalize_target_nodes(requested_nodes)
            target_nodes = normalized_nodes
        elif resume_node in VALID_RESUME_NODES and resume_node != "solver":
            start_index = WORKFLOW_ORDER.index(resume_node)
            target_nodes = WORKFLOW_ORDER[start_index:]
        else:
            target_nodes = None

        effective_draft_solution = req.draft_solution
        if effective_draft_solution is not None:
            effective_draft_solution = effective_draft_solution.strip() or None
        if requires_draft_for_entry_point(resume_node, effective_draft_solution):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="draft_solution is required when entry_point is reviewer or formatter.",
            )

        new_task = Task(
            task_id=new_task_id,
            thread_id=new_thread_id,
            image_url=normalized_image_urls[0] if normalized_image_urls else "",
            image_urls_json=json.dumps(normalized_image_urls, ensure_ascii=False),
            question_text=normalized_question_text or None,
            input_revision=1,
            workflow_type="standard",
            state=TaskStatus.QUEUED.value,
            history=json.dumps(
                {
                    "image_urls": normalized_image_urls,
                    "workflow_template_id": workflow_template_id,
                    "draft_solution": effective_draft_solution,
                    "question_text": normalized_question_text or None,
                    "question_content": normalized_question_text or None,
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


@app.get("/api/tasks/active", response_model=list[TaskDetailResponse])
@app.get("/api/tasks/active/list", response_model=list[TaskDetailResponse])
def list_active_tasks(db: Session = Depends(get_db)):
    """Return every unfinished workflow, including target-system imports."""
    return (
        db.query(Task)
        .filter(
            Task.state != TaskStatus.COMPLETED.value,
            or_(
                Task.workflow_type != "errata",
                Task.state != TaskStatus.MANUAL.value,
            ),
        )
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .all()
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
            detail=f"Task with id {task_id} not found.",
        )
    return task


@app.get(
    "/api/tasks/{task_id}/artifacts", response_model=list[TaskArtifactResponse]
)
def list_task_artifacts(task_id: str, db: Session = Depends(get_db)):
    if not db.query(Task).filter(Task.task_id == task_id).first():
        raise HTTPException(status_code=404, detail="Task not found.")
    return (
        db.query(TaskArtifact)
        .filter(TaskArtifact.task_id == task_id)
        .order_by(TaskArtifact.input_revision, TaskArtifact.id)
        .all()
    )


@app.patch("/api/tasks/{task_id}/input", response_model=TaskDetailResponse)
def update_task_input(
    task_id: str,
    req: TaskInputUpdateRequest,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    current_images = task.image_urls
    incoming_images = normalize_image_urls(req.image_urls)
    incoming_text = (req.question_text or "").strip()
    if req.mode == "replace":
        next_images = incoming_images
        next_text = incoming_text
    else:
        next_images = normalize_image_urls(current_images + incoming_images)
        current_text = (task.question_text or "").strip()
        next_text = "\n".join(part for part in [current_text, incoming_text] if part)
    if not next_images and not next_text:
        raise HTTPException(status_code=400, detail="更新后题目图片和文字不能同时为空。")
    task.image_urls_json = json.dumps(next_images, ensure_ascii=False)
    task.image_url = next_images[0] if next_images else ""
    task.question_text = next_text or None
    task.input_revision = int(task.input_revision or 1) + 1
    task.state = TaskStatus.MANUAL.value
    task.current_node = None
    task.final_result = None
    task.question_preview = None
    task.answer_preview = None
    task.error_code = None
    try:
        history = json.loads(task.history or "{}")
    except Exception:
        history = {}
    history["image_urls"] = next_images
    history["question_text"] = next_text or None
    history.pop("draft_solution", None)
    history.pop("review_decision", None)
    history.pop("review_feedback", None)
    task.history = json.dumps(history, ensure_ascii=False)
    db.commit()
    db.refresh(task)
    return task


@app.post("/api/tasks/{task_id}/run", status_code=202)
def run_task_from_node(
    task_id: str,
    req: TaskRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.state in {
        TaskStatus.QUEUED.value,
        TaskStatus.SOLVING.value,
        TaskStatus.REVIEWING.value,
        TaskStatus.FORMATTING.value,
    }:
        raise HTTPException(status_code=409, detail="任务正在运行，请先等待当前节点结束。")
    node_order = workflow_node_order(task.workflow_type or "standard")
    targets = (
        validate_requested_target_nodes(req.target_nodes, task.workflow_type or "standard")
        if req.target_nodes
        else node_order[node_order.index(req.start_node):] if req.start_node in node_order else []
    )
    if not targets or targets[0] != req.start_node:
        raise HTTPException(status_code=400, detail="target_nodes 必须按顺序且以 start_node 开始。")
    if req.start_node in {"reviewer", "formatter", "errata_adjudication", "word_composition"}:
        predecessor = (
            "formatter"
            if task.workflow_type == "errata" and req.start_node in {"errata_adjudication", "word_composition"}
            else "solver"
        )
        artifact = latest_task_artifact(task_id, predecessor, int(task.input_revision or 1))
        if not artifact:
            raise HTTPException(status_code=409, detail=f"当前输入版本没有 {predecessor} 产物，不能从该节点启动。")
        try:
            history = json.loads(task.history or "{}")
        except Exception:
            history = {}
        if predecessor == "formatter":
            history["formatted_solution"] = artifact.content
        else:
            history["draft_solution"] = artifact.content
        task.history = json.dumps(history, ensure_ascii=False)
    if task.workflow_type == "errata" and req.start_node == "word_composition":
        review_artifact = latest_task_artifact(
            task_id, "errata_adjudication", int(task.input_revision or 1)
        )
        if not review_artifact:
            raise HTTPException(status_code=409, detail="勘误 Word 写回编排只能使用裁决通过的产物。")
        try:
            review_data = json.loads(review_artifact.content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=409, detail="勘误裁决产物格式无效。") from exc
        if review_data.get("original_answer_verdict") == "insufficient_evidence":
            raise HTTPException(status_code=409, detail="证据不足，不能自动生成 Word 勘误文本。")
    task.state = TaskStatus.QUEUED.value
    task.error_code = None
    if req.start_node != node_order[-1]:
        task.final_result = None
        task.question_preview = None
        task.answer_preview = None
    db.commit()
    background_tasks.add_task(run_agent_workflow_async, task_id, req.start_node, targets)
    return {"status": "accepted", "start_node": req.start_node, "target_nodes": targets}


@app.get("/api/task-inbox")
def list_task_inbox(
    workflow_type: str | None = Query(default=None),
    state: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    needs_attention: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """统一返回可恢复任务；工作流和来源筛选使用 Task 显式字段。"""
    rows = db.query(Task).order_by(Task.updated_at.desc(), Task.created_at.desc()).limit(limit).all()
    task_ids = [task.task_id for task in rows]
    errata_map = {item.task_id: item for item in db.query(ErrataItem).filter(ErrataItem.task_id.in_(task_ids)).all() if item.task_id}
    errata_job_ids = {item.job_id for item in errata_map.values()}
    errata_jobs = {job.job_id: job for job in db.query(ErrataJob).filter(ErrataJob.job_id.in_(errata_job_ids)).all()} if errata_job_ids else {}
    items = []
    for task in rows:
        try:
            meta = json.loads(task.history or "{}")
        except Exception:
            meta = {}
        kind = task.workflow_type or "standard"
        if kind == "errata" and task.state == "completed":
            continue
        legacy_errata = errata_map.get(task.task_id)
        if kind == "errata" and legacy_errata:
            job = errata_jobs.get(legacy_errata.job_id)
            attachment_urls = []
            for relative_path in json.loads(legacy_errata.evidence_json or "[]"):
                parts = Path(relative_path).parts
                if len(parts) >= 2 and parts[0] in {"render", "evidence"}:
                    attachment_urls.append(f"/api/errata/jobs/{legacy_errata.job_id}/evidence/{parts[0]}/{Path(relative_path).name}")
            meta = {
                **meta,
                "source_kind": "errata",
                "source_id": legacy_errata.job_id,
                "source_title": job.original_filename if job else "勘误任务",
                "source_item_label": legacy_errata.source_ref or f"题块 {legacy_errata.item_index}",
                "errata_item_id": legacy_errata.item_id,
                "attachment_urls": attachment_urls,
            }
        source_title = str(meta.get("source_title") or "普通解题")
        source_item_label = str(meta.get("source_item_label") or task.task_id)
        searchable = " ".join([task.task_id, task.question_text or "", source_title, source_item_label]).lower()
        requested_workflow = {
            "normal": "standard",
            "paper": "full_paper",
            "target_system": "automation",
        }.get(workflow_type or "", workflow_type)
        if requested_workflow and kind != requested_workflow:
            continue
        if state and task.state != state:
            continue
        if keyword and keyword.lower() not in searchable:
            continue
        if needs_attention and task.state not in {"manual", "failed", "paused", "terminated", "abandoned"}:
            continue
        items.append({
            "task_id": task.task_id,
            "workflow_type": kind,
            "state": task.state,
            "source_kind": task.source_kind or meta.get("source_kind") or kind,
            "source_id": task.source_id or meta.get("source_id"),
            "source_title": source_title,
            "source_item_label": source_item_label,
            "attachment_urls": meta.get("attachment_urls") or task.image_urls,
            "resume_target": {
                "view": "errata" if kind == "errata" else "paper-docx" if kind == "full_paper" else "target-system" if kind == "automation" else "admin",
                "source_id": task.source_id or meta.get("source_id"),
                "item_id": task.source_item_id or meta.get("errata_item_id") or meta.get("paper_question_id"),
            },
            "error_code": task.error_code,
            "updated_at": task.updated_at,
            "created_at": task.created_at,
        })
    return {"total": len(items), "items": items}


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
            TaskStatus.PAUSED.value,
            TaskStatus.TERMINATED.value,
            TaskStatus.ABANDONED.value,
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
            TaskStatus.PAUSED.value,
            TaskStatus.TERMINATED.value,
            TaskStatus.ABANDONED.value,
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
    workflow_type = task.workflow_type or "standard"
    node_order = workflow_node_order(workflow_type)
    draft_artifact_node = "solver"
    if not current_history.get("draft_solution"):
        solver_artifact = latest_task_artifact(
            task_id, draft_artifact_node, int(task.input_revision or 1)
        )
        if solver_artifact:
            current_history["draft_solution"] = solver_artifact.content
    if req.draft_solution is not None:
        current_history["draft_solution"] = req.draft_solution

    target_nodes: list[str] | None = None
    if req.action == "skip_review":
        if workflow_type == "errata":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="勘误工作流不能跳过勘误裁决节点。",
            )
        if current_history.get("draft_solution"):
            resume_node = "formatter"
            target_nodes = ["formatter"]
            current_history["failed_node"] = "reviewer"
        else:
            resume_node = "solver"
            target_nodes = ["solver", "formatter"]
            current_history["failed_node"] = "reviewer"
    elif req.action == "custom_run":
        if not req.entry_point:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="entry_point is required when action=custom_run.",
            )
        requested_nodes = validate_requested_target_nodes(req.target_nodes, workflow_type)
        if not requested_nodes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_nodes is required when action=custom_run.",
            )
        if req.entry_point not in requested_nodes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="entry_point must be included in target_nodes.",
            )

        expected_entry = requested_nodes[0]
        if req.entry_point != expected_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"entry_point must be the first node in target_nodes: {expected_entry}.",
            )

        if requires_draft_for_entry_point(req.entry_point, current_history.get("draft_solution")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="draft_solution is required when entry_point is reviewer or formatter.",
            )

        resume_node = req.entry_point
        normalized_nodes = normalize_target_nodes(requested_nodes, workflow_type)
        target_nodes = normalized_nodes
        current_history["target_nodes"] = normalized_nodes
    else:
        resume_node = current_history.get("failed_node", node_order[0])
        if resume_node not in node_order:
            resume_node = node_order[0]
        if requires_draft_for_entry_point(resume_node, current_history.get("draft_solution")):
            resume_node = node_order[0]
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


@app.post("/api/tasks/{task_id}/operation", response_model=TaskDetailResponse)
def operate_task(task_id: str, req: TaskOperationRequest, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.state == TaskStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail="已完成任务不能执行该操作。")
    state_by_action = {
        "pause": TaskStatus.PAUSED.value,
        "terminate": TaskStatus.TERMINATED.value,
        "abandon": TaskStatus.ABANDONED.value,
    }
    label_by_action = {"pause": "暂停", "terminate": "终止", "abandon": "放弃"}
    task.state = state_by_action[req.action]
    task.error_code = f"用户已{label_by_action[req.action]}任务。"
    db.commit()
    db.refresh(task)
    return task


@app.get("/api/settings/runtime", response_model=RuntimeSettingsResponse)
def get_runtime_settings():
    return read_public_runtime_settings()


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
    try:
        is_errata_task = json.loads(task.history or "{}").get("workflow_type") == "errata"
    except Exception:
        is_errata_task = False
    if is_errata_task:
        from app.services.errata_service import run_errata_task

        task.state = TaskStatus.QUEUED.value
        task.error_code = None
        db.commit()
        background_tasks.add_task(run_errata_task, task_id)
        return {"status": "success", "message": "勘误任务已继续。"}

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
    workflow_type: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(Task)
    if task_id:
        query = query.filter(Task.task_id.like(f"%{task_id}%"))
    if state:
        query = query.filter(Task.state == state.value)
    if workflow_type:
        query = query.filter(Task.workflow_type == workflow_type)
    if source_id:
        query = query.filter(Task.source_id == source_id)

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

    db.query(ErrataItem).filter(ErrataItem.task_id == task_id).delete(
        synchronize_session=False
    )
    db.query(AgentLog).filter(AgentLog.task_id == task_id).delete()
    db.query(TaskArtifact).filter(TaskArtifact.task_id == task_id).delete()
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
            if "question_text" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN question_text TEXT"))
            if "image_urls_json" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN image_urls_json TEXT"))
            if "input_revision" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN input_revision INTEGER NOT NULL DEFAULT 1"))
            if "current_node" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN current_node VARCHAR"))
            if "workflow_type" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN workflow_type VARCHAR NOT NULL DEFAULT 'standard'"))
            if "source_kind" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN source_kind VARCHAR"))
            if "source_id" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN source_id VARCHAR"))
            if "source_item_id" not in columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN source_item_id VARCHAR"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_workflow_type ON tasks (workflow_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_source_kind ON tasks (source_kind)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_source_id ON tasks (source_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_source_item_id ON tasks (source_item_id)"))
            errata_columns = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(errata_jobs)")).fetchall()
            }
            if errata_columns:
                if "mineru_status" not in errata_columns:
                    conn.execute(text("ALTER TABLE errata_jobs ADD COLUMN mineru_status VARCHAR DEFAULT 'not_requested'"))
                if "mineru_markdown" not in errata_columns:
                    conn.execute(text("ALTER TABLE errata_jobs ADD COLUMN mineru_markdown TEXT"))
                if "custom_anchors" not in errata_columns:
                    conn.execute(text("ALTER TABLE errata_jobs ADD COLUMN custom_anchors TEXT"))
            item_columns = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(errata_items)")).fetchall()
            }
            if item_columns:
                if "mineru_text" not in item_columns:
                    conn.execute(text("ALTER TABLE errata_items ADD COLUMN mineru_text TEXT"))
                if "review_status" not in item_columns:
                    conn.execute(text("ALTER TABLE errata_items ADD COLUMN review_status VARCHAR DEFAULT 'pending'"))
                if "review_feedback" not in item_columns:
                    conn.execute(text("ALTER TABLE errata_items ADD COLUMN review_feedback TEXT"))
                if "task_id" not in item_columns:
                    conn.execute(text("ALTER TABLE errata_items ADD COLUMN task_id VARCHAR"))
                if "question_material_paths_json" not in item_columns:
                    conn.execute(text("ALTER TABLE errata_items ADD COLUMN question_material_paths_json TEXT"))
            paper_columns = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(paper_projects)")).fetchall()
            }
            if paper_columns:
                if "mineru_status" not in paper_columns:
                    conn.execute(text("ALTER TABLE paper_projects ADD COLUMN mineru_status VARCHAR DEFAULT 'not_requested'"))
                if "mineru_markdown" not in paper_columns:
                    conn.execute(text("ALTER TABLE paper_projects ADD COLUMN mineru_markdown TEXT"))
    except Exception as exc:
        print(f"[DB] Failed to ensure preview columns: {exc}")


def migrate_task_workflow_metadata() -> None:
    """一次性把 history 中的工作流来源迁到 Task 显式字段。"""
    migration_id = "task_workflow_metadata_v1"
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS app_migrations "
                    "(migration_id VARCHAR PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            applied = conn.execute(
                text("SELECT 1 FROM app_migrations WHERE migration_id = :migration_id"),
                {"migration_id": migration_id},
            ).first()
        if applied:
            return

        workflow_map = {
            "normal": "standard",
            "standard": "standard",
            "errata": "errata",
            "paper": "full_paper",
            "full_paper": "full_paper",
            "target_system": "automation",
            "automation": "automation",
        }
        with SessionLocal() as db:
            for task in db.query(Task).all():
                try:
                    history = json.loads(task.history or "{}")
                except Exception:
                    history = {}
                task.workflow_type = workflow_map.get(
                    str(history.get("workflow_type") or task.workflow_type or "standard"),
                    "standard",
                )
                task.source_kind = task.source_kind or history.get("source_kind")
                task.source_id = task.source_id or (
                    str(history.get("source_id")) if history.get("source_id") is not None else None
                )
                source_item = (
                    history.get("errata_item_id")
                    or history.get("paper_question_id")
                    or history.get("target_system_remote_id")
                )
                task.source_item_id = task.source_item_id or (
                    str(source_item) if source_item is not None else None
                )
            for job_id, in db.query(ErrataJob.job_id).all():
                ensure_errata_tasks(db, job_id, create_missing=True)
            db.execute(
                text("INSERT INTO app_migrations (migration_id) VALUES (:migration_id)"),
                {"migration_id": migration_id},
            )
            db.commit()
    except Exception as exc:
        print(f"[DB] Failed to migrate task workflow metadata: {exc}")


def ensure_target_system_columns() -> None:
    """为已有 SQLite 数据库补齐串行交付字段。"""
    try:
        with engine.begin() as conn:
            columns = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(target_system_tasks)")).fetchall()
            }
            additions = {
                "delivery_order": "INTEGER",
                "delivery_locked_at": "DATETIME",
                "filled_at": "DATETIME",
                "delivered_at": "DATETIME",
                "browser_screenshot_path": "TEXT",
                "rendered_answer_path": "TEXT",
            }
            for name, sql_type in additions.items():
                if columns and name not in columns:
                    conn.execute(text(f"ALTER TABLE target_system_tasks ADD COLUMN {name} {sql_type}"))
    except Exception as exc:
        print(f"[DB] Failed to ensure target system columns: {exc}")


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


# 挂载前端静态文件（SPA 支持）
# 在所有 API 路由之后挂载，确保 /api/* 优先匹配
frontend_dist_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dist_path):
    app.mount(
        "/", StaticFiles(directory=frontend_dist_path, html=True), name="frontend"
    )
