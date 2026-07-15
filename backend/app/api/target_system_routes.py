"""目标系统：只读/抢题 API 与严格串行网页交付队列。"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, text, update

from app.core.database import SessionLocal
from app.models.domain import Task, TargetSystemDeliveryLock, TargetSystemTask
from app.services.target_system_client import TargetSystemClient


router = APIRouter(prefix="/api/target-system", tags=["target-system"])
DELIVERY_ROOT = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[3] / "data")) / "target-system-delivery"
ACTIVE_DELIVERY_STATES = {"filling", "awaiting_user_submit", "fill_failed"}
BROWSER_ACCESS_URL = os.getenv("TARGET_SYSTEM_BROWSER_ACCESS_URL", "").strip()
sync_lock = asyncio.Lock()
sync_status = {"state": "idle", "synced": 0, "imported": 0, "schools_done": 0, "schools_total": 0, "error": ""}


class RemoteSelectionRequest(BaseModel):
    remote_task_ids: list[str] = Field(default_factory=list)


class RemoteSyncRequest(BaseModel):
    school_id: int | None = None
    subject_id: int | None = None


class ConfirmReviewRequest(BaseModel):
    exam_point: str = ""


class DeliveryErrorRequest(BaseModel):
    error_message: str = Field(min_length=1, max_length=2000)


def extract_delivery_content(task: Task) -> tuple[str, str]:
    """Split the reviewed solution into the OCR body and target-system exam point."""
    source = (task.answer_preview or task.final_result or "").strip()
    marker = "【考点延伸】"
    marker_index = source.find(marker)
    if marker_index < 0:
        raise ValueError("最终答案缺少【考点延伸】区块。")
    answer_body = source[:marker_index].strip()
    exam_point = source[marker_index + len(marker):].strip()
    if not answer_body:
        raise ValueError("【考点延伸】前缺少可识别录入的答案正文。")
    if not exam_point:
        raise ValueError("【考点延伸】区块不能为空。")
    return answer_body, exam_point


def _first_text(value: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _remote_id(value: dict[str, Any]) -> str:
    for key in ("id", "topic_id", "ai_topic_id", "aiTopicId"):
        item = value.get(key)
        if item is not None and str(item).strip():
            return str(item).strip()
    raise ValueError("远端题目缺少 id")


def _find_image_urls(value: Any, key_hint: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_find_image_urls(item, str(key).lower()))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_image_urls(item, key_hint))
    elif isinstance(value, str):
        candidate = value.strip()
        is_image_key = any(token in key_hint for token in ("image", "img", "pic", "photo", "figure"))
        is_image_url = candidate.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        if candidate.startswith(("http://", "https://")) and (is_image_key or is_image_url):
            found.append(candidate)
    return list(dict.fromkeys(found))[:12]


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _task_to_dict(item: TargetSystemTask, task: Task | None = None, include_detail: bool = False) -> dict[str, Any]:
    try:
        source = json.loads(item.source_json or "{}")
    except json.JSONDecodeError:
        source = {}
    paper = source.get("paperInfo") if isinstance(source, dict) else {}
    question_text = _first_text(source, ("topic_text", "topic", "question_text", "question", "content"))
    display_title = question_text.splitlines()[0][:160] if question_text else item.title or "未命名题目"
    result = {
        "id": item.id,
        "remote_task_id": item.remote_task_id,
        "title": display_title,
        "status": item.status,
        "workflow_task_id": item.workflow_task_id,
        "workflow_state": task.state if task else None,
        "exam_point": item.exam_point or "",
        "delivery_order": item.delivery_order,
        "error_message": item.error_message,
        "browser_screenshot_path": item.browser_screenshot_path,
        "browser_screenshot_url": f"/api/target-system/tasks/{item.id}/artifact/page" if item.browser_screenshot_path else None,
        "rendered_answer_url": f"/api/target-system/tasks/{item.id}/artifact/answer" if item.rendered_answer_path else None,
        "school_id": paper.get("school_id") if isinstance(paper, dict) else None,
        "school_name": (paper.get("school_name") or "").strip() if isinstance(paper, dict) else "",
        "subject_id": paper.get("subject_id") if isinstance(paper, dict) else None,
        "subject_name": (paper.get("subject_name") or "").strip() if isinstance(paper, dict) else "",
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_detail:
        result["question_text"] = question_text
        result["image_urls"] = _find_image_urls(source)
    return result


def _refresh_review_states(db) -> None:
    candidates = db.query(TargetSystemTask).filter(TargetSystemTask.status.in_(("solving", "claimed"))).all()
    changed = False
    for item in candidates:
        if not item.workflow_task_id:
            continue
        task = db.query(Task).filter(Task.task_id == item.workflow_task_id).first()
        if task and task.state == "completed" and (task.final_result or "").strip():
            item.status = "review_pending"
            changed = True
    if changed:
        db.commit()


def _lock_for_task(db, item_id: int) -> TargetSystemDeliveryLock | None:
    return db.query(TargetSystemDeliveryLock).filter(TargetSystemDeliveryLock.target_task_id == item_id).first()


@router.get("/filters")
async def list_target_filters():
    with SessionLocal() as db:
        subject_rows = db.query(
            func.json_extract(TargetSystemTask.source_json, "$.paperInfo.subject_id"),
            func.json_extract(TargetSystemTask.source_json, "$.paperInfo.subject_name"),
        ).distinct().all()
        local_school_rows = db.query(
            func.json_extract(TargetSystemTask.source_json, "$.paperInfo.school_id"),
            func.json_extract(TargetSystemTask.source_json, "$.paperInfo.school_name"),
        ).distinct().all()
    schools = {str(item_id): {"id": item_id, "name": str(name or f"学校{item_id}")} for item_id, name in local_school_rows if item_id is not None}
    try:
        for school in await TargetSystemClient().list_schools():
            school_id = school.get("school_id")
            if school_id is not None:
                schools[str(school_id)] = {"id": school_id, "name": str(school.get("school_name") or f"学校{school_id}")}
    except Exception:
        pass
    subjects = [{"id": item_id, "name": str(name)} for item_id, name in subject_rows if item_id is not None and name]
    return {"schools": sorted(schools.values(), key=lambda item: item["name"]), "subjects": sorted(subjects, key=lambda item: item["name"])}


@router.get("/tasks")
def list_target_tasks(status: str | None = None, school_id: int | None = None, subject_id: int | None = None, page: int = 1, page_size: int = 20):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    with SessionLocal() as db:
        _refresh_review_states(db)
        query = db.query(TargetSystemTask)
        if status:
            query = query.filter(TargetSystemTask.status == status)
        if school_id is not None:
            query = query.filter(func.json_extract(TargetSystemTask.source_json, "$.paperInfo.school_id") == school_id)
        if subject_id is not None:
            query = query.filter(func.json_extract(TargetSystemTask.source_json, "$.paperInfo.subject_id") == subject_id)
        total = query.count()
        items = query.order_by(TargetSystemTask.delivery_order.asc().nullslast(), TargetSystemTask.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
        task_ids = [item.workflow_task_id for item in items if item.workflow_task_id]
        task_map = {
            task.task_id: task
            for task in db.query(Task).filter(Task.task_id.in_(task_ids)).all()
        } if task_ids else {}
        lock = db.query(TargetSystemDeliveryLock).filter(TargetSystemDeliveryLock.id == 1).first()
        return {
            "items": [_task_to_dict(item, task_map.get(item.workflow_task_id or "")) for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "locked_task_id": lock.target_task_id if lock else None,
        }


@router.get("/tasks/{item_id}/detail")
def get_target_task_detail(item_id: int):
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="目标系统题目不存在。")
        task = db.query(Task).filter(Task.task_id == item.workflow_task_id).first() if item.workflow_task_id else None
        return _task_to_dict(item, task, include_detail=True)


@router.get("/tasks/{item_id}/artifact/{kind}")
def get_target_task_artifact(item_id: int, kind: str):
    if kind not in {"answer", "page"}:
        raise HTTPException(status_code=404, detail="交付产物不存在。")
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        path = item.rendered_answer_path if item and kind == "answer" else item.browser_screenshot_path if item else None
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="交付产物不存在。")
    return FileResponse(path, media_type="image/png")


async def _run_remote_sync(school_id: int | None = None, subject_id: int | None = None) -> None:
    global sync_status
    async with sync_lock:
        sync_status = {"state": "running", "synced": 0, "imported": 0, "schools_done": 0, "schools_total": 0, "error": ""}
        try:
            client = TargetSystemClient()
            async for batch in client.iter_pending_task_batches(school_id=school_id, subject_ids=[subject_id] if subject_id else None):
                batch_imported = 0
                with SessionLocal() as db:
                    for raw in batch["rows"]:
                        try:
                            remote_task_id = _remote_id(raw)
                        except ValueError:
                            continue
                        item = db.query(TargetSystemTask).filter(TargetSystemTask.remote_task_id == remote_task_id).first()
                        if item:
                            continue
                        title = _first_text(raw, ("title", "topic", "topic_text", "question", "content"))[:160]
                        db.add(TargetSystemTask(remote_task_id=remote_task_id, title=title or "远端题目", source_json=json.dumps(raw, ensure_ascii=False), status="discovered"))
                        batch_imported += 1
                    db.commit()
                sync_status = {
                    "state": "running",
                    "synced": sync_status["synced"] + len(batch["rows"]),
                    "imported": sync_status["imported"] + batch_imported,
                    "schools_done": batch["school_index"],
                    "schools_total": batch["school_total"],
                    "error": "",
                }
        except Exception as exc:
            sync_status = {**sync_status, "state": "failed", "error": str(exc)[:500]}
            return
        sync_status = {**sync_status, "state": "completed", "error": ""}


@router.post("/sync", status_code=202)
async def sync_remote_tasks(req: RemoteSyncRequest, background_tasks: BackgroundTasks):
    if sync_lock.locked():
        return {**sync_status, "mode": "api_school_pagination"}
    if req.school_id is None:
        raise HTTPException(status_code=400, detail="请先选择学校，再同步待接题。")
    background_tasks.add_task(_run_remote_sync, req.school_id, req.subject_id)
    return {"state": "accepted", "synced": 0, "imported": 0, "schools_done": 0, "schools_total": 0, "error": "", "mode": "api_school_pagination"}


@router.get("/sync/status")
def get_sync_status():
    return {**sync_status, "mode": "api_school_pagination"}


@router.post("/tasks/select")
def select_remote_tasks(req: RemoteSelectionRequest):
    ids = {item.strip() for item in req.remote_task_ids if item.strip()}
    with SessionLocal() as db:
        items = db.query(TargetSystemTask).filter(TargetSystemTask.remote_task_id.in_(ids)).all() if ids else []
        for item in items:
            if item.status == "discovered":
                item.status = "selected"
        db.commit()
    return {"selected": len(items)}


@router.post("/tasks/claim", status_code=202)
async def claim_selected_tasks(req: RemoteSelectionRequest, background_tasks: BackgroundTasks):
    from app.main import run_agent_workflow_async

    requested = {item.strip() for item in req.remote_task_ids if item.strip()}
    if not requested:
        raise HTTPException(status_code=400, detail="请至少选择一题。")
    client = TargetSystemClient()
    started: list[str] = []
    with SessionLocal() as db:
        selected = db.query(TargetSystemTask).filter(TargetSystemTask.remote_task_id.in_(requested)).all()
        for item in selected:
            if item.status not in {"discovered", "selected"}:
                continue
            try:
                await client.claim(item.remote_task_id)
                detail = await client.detail(item.remote_task_id)
                image_urls = _find_image_urls(detail)
                target_dir = DELIVERY_ROOT / "source" / item.remote_task_id
                target_dir.mkdir(parents=True, exist_ok=True)
                local_images: list[Path] = []
                for index, image_url in enumerate(image_urls):
                    try:
                        suffix = Path(image_url.split("?", 1)[0]).suffix or ".png"
                        output = target_dir / f"question-{index + 1}{suffix}"
                        output.write_bytes(await client.download(image_url))
                        local_images.append(output)
                    except Exception:
                        continue
                task_id = f"target_{uuid.uuid4().hex[:12]}"
                question_text = _first_text(detail, ("topic_text", "topic", "question_text", "question", "content"))
                data_urls = [_data_url(path) for path in local_images]
                history = {
                    "workflow_type": "target_system",
                    "image_urls": data_urls,
                    "question_text": question_text,
                    "target_system_remote_id": item.remote_task_id,
                    "source_kind": "target_system",
                    "source_id": str(item.id),
                    "source_title": item.title,
                    "source_item_label": item.remote_task_id,
                }
                db.add(Task(task_id=task_id, thread_id=task_id, image_url=data_urls[0] if data_urls else "", image_urls_json=json.dumps(data_urls, ensure_ascii=False), question_text=question_text or None, input_revision=1, workflow_type="automation", source_kind="target_system", source_id=str(item.id), source_item_id=item.remote_task_id, state="queued", retry_count=0, history=json.dumps(history, ensure_ascii=False)))
                item.source_json = json.dumps(detail, ensure_ascii=False)
                item.title = _first_text(detail, ("title", "topic", "topic_text", "question", "content"))[:160] or item.title
                item.image_paths_json = json.dumps([str(path) for path in local_images], ensure_ascii=False)
                item.workflow_task_id = task_id
                item.status = "solving"
                item.error_message = None
                item.browser_screenshot_path = None
                item.rendered_answer_path = None
                started.append(task_id)
            except Exception as exc:
                item.status = "discovered"
                item.error_message = f"抢题或导入失败：{str(exc)[:500]}"
        db.commit()
    for task_id in started:
        background_tasks.add_task(run_agent_workflow_async, task_id, "solver", ["solver", "reviewer", "formatter"])
    return {"started_task_ids": started}


@router.post("/tasks/{item_id}/confirm-review")
def confirm_review(item_id: int, req: ConfirmReviewRequest):
    with SessionLocal() as db:
        _refresh_review_states(db)
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        if not item or not item.workflow_task_id:
            raise HTTPException(status_code=404, detail="目标系统题目不存在。")
        task = db.query(Task).filter(Task.task_id == item.workflow_task_id).first()
        if not task or task.state != "completed" or not (task.final_result or "").strip():
            raise HTTPException(status_code=409, detail="请先完成并检查最终排版结果。")
        try:
            _, exam_point = extract_delivery_content(task)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        item.exam_point = exam_point
        item.status = "ready_to_fill"
        if item.delivery_order is None:
            max_order = db.query(TargetSystemTask.delivery_order).order_by(TargetSystemTask.delivery_order.desc()).first()
            item.delivery_order = (max_order[0] if max_order and max_order[0] is not None else 0) + 1
        db.commit()
        return _task_to_dict(item, task)


@router.post("/tasks/{item_id}/return-to-all")
def return_task_to_all(item_id: int):
    """撤回本地工作流，不向远端目标系统发送取消或放弃请求。"""
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="目标系统题目不存在。")
        if item.status == "delivered":
            raise HTTPException(status_code=409, detail="已标记为已交付的题目不能撤回。")

        if item.workflow_task_id:
            task = db.query(Task).filter(Task.task_id == item.workflow_task_id).first()
            if task and task.state not in {"completed", "cancelled", "failed", "abandoned"}:
                task.state = "abandoned"
                task.error_code = "已从目标系统本地工作流撤回。"

        lock = _lock_for_task(db, item.id)
        if lock:
            lock.target_task_id = None
        item.workflow_task_id = None
        item.status = "discovered"
        item.exam_point = None
        item.delivery_order = None
        item.delivery_locked_at = None
        item.filled_at = None
        item.error_message = None
        item.browser_screenshot_path = None
        item.rendered_answer_path = None
        db.commit()
        return _task_to_dict(item)


@router.post("/delivery/fill-next")
def reserve_next_delivery():
    with SessionLocal() as db:
        db.execute(text("INSERT OR IGNORE INTO target_system_delivery_locks (id) VALUES (1)"))
        db.commit()
        lock = db.query(TargetSystemDeliveryLock).filter(TargetSystemDeliveryLock.id == 1).first()
        if lock and lock.target_task_id:
            active = db.query(TargetSystemTask).filter(TargetSystemTask.id == lock.target_task_id).first()
            if active and active.status in ACTIVE_DELIVERY_STATES:
                raise HTTPException(status_code=409, detail="当前题目尚未完成网页提交，不能填入下一题。")
            lock.target_task_id = None
            db.commit()
        candidate = db.query(TargetSystemTask).filter(TargetSystemTask.status == "ready_to_fill").order_by(TargetSystemTask.delivery_order.asc(), TargetSystemTask.id.asc()).first()
        if not candidate:
            raise HTTPException(status_code=404, detail="没有待填网页的已确认题目。")
        claimed = db.execute(update(TargetSystemDeliveryLock).where(TargetSystemDeliveryLock.id == 1, TargetSystemDeliveryLock.target_task_id.is_(None)).values(target_task_id=candidate.id))
        if claimed.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="交付队列已被另一窗口占用。")
        candidate.status = "filling"
        candidate.delivery_locked_at = datetime.now(timezone.utc)
        candidate.error_message = None
        db.commit()
        return {"item_id": candidate.id, "status": candidate.status}


@router.get("/delivery/current")
def current_delivery():
    with SessionLocal() as db:
        lock = db.query(TargetSystemDeliveryLock).filter(TargetSystemDeliveryLock.id == 1).first()
        if not lock or not lock.target_task_id:
            return {"item": None}
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == lock.target_task_id).first()
        if not item:
            lock.target_task_id = None
            db.commit()
            return {"item": None}
        task = db.query(Task).filter(Task.task_id == item.workflow_task_id).first() if item.workflow_task_id else None
        if item.status != "filling" or not task:
            return {"item": _task_to_dict(item, task)}
        try:
            answer_markdown, exam_point = extract_delivery_content(task)
        except ValueError as exc:
            return {
                "item": {
                    **_task_to_dict(item, task),
                    "answer_markdown": "",
                    "delivery_content_error": str(exc),
                    "remote_task_id": item.remote_task_id,
                }
            }
        return {
            "item": {
                **_task_to_dict(item, task),
                "answer_markdown": answer_markdown,
                "exam_point": exam_point,
                "remote_task_id": item.remote_task_id,
            }
        }


@router.get("/delivery/session")
def delivery_browser_session():
    return {"access_url": BROWSER_ACCESS_URL or None}


def _save_upload(item_id: int, label: str, upload: UploadFile | None) -> str | None:
    if not upload or not hasattr(upload, "file"):
        return None
    DELIVERY_ROOT.mkdir(parents=True, exist_ok=True)
    filename = f"{item_id}-{label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    output = DELIVERY_ROOT / filename
    output.write_bytes(upload.file.read())
    return str(output)


@router.post("/delivery/{item_id}/filled")
def mark_filled(
    item_id: int,
    screenshot: UploadFile | None = File(default=None),
    rendered_answer: UploadFile | None = File(default=None),
):
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        lock = _lock_for_task(db, item_id)
        if not item or not lock or item.status != "filling":
            raise HTTPException(status_code=409, detail="当前题目不在等待浏览器填入状态。")
        snapshot = _save_upload(item_id, "page", screenshot)
        answer_image = _save_upload(item_id, "answer", rendered_answer)
        if snapshot:
            item.browser_screenshot_path = snapshot
        if answer_image:
            item.rendered_answer_path = answer_image
        item.status = "awaiting_user_submit"
        item.filled_at = datetime.now(timezone.utc)
        db.commit()
        return _task_to_dict(item)


@router.post("/delivery/{item_id}/failed")
def mark_fill_failed(item_id: int, error_message: str = Form(...), screenshot: UploadFile | None = File(default=None)):
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        lock = _lock_for_task(db, item_id)
        if not item or not lock or item.status != "filling":
            raise HTTPException(status_code=409, detail="当前题目不在浏览器填入状态。")
        snapshot = _save_upload(item_id, "failure", screenshot)
        if snapshot:
            item.browser_screenshot_path = snapshot
        item.status = "fill_failed"
        item.error_message = error_message[:2000]
        db.commit()
        return _task_to_dict(item)


@router.post("/delivery/{item_id}/retry")
def retry_fill(item_id: int):
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        lock = _lock_for_task(db, item_id)
        if not item or not lock or item.status not in {"fill_failed", "awaiting_user_submit"}:
            raise HTTPException(status_code=409, detail="只能重试当前锁定的填入失败题目。")
        item.status = "filling"
        item.error_message = None
        item.delivery_locked_at = datetime.now(timezone.utc)
        db.commit()
        return _task_to_dict(item)


@router.post("/delivery/{item_id}/delivered")
def mark_delivered(item_id: int):
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        lock = _lock_for_task(db, item_id)
        if not item or not lock or item.status != "awaiting_user_submit":
            raise HTTPException(status_code=409, detail="请先在网页手动提交，再标记为已交付。")
        item.status = "delivered"
        item.delivered_at = datetime.now(timezone.utc)
        lock.target_task_id = None
        db.commit()
        return _task_to_dict(item)


@router.post("/delivery/{item_id}/abandon")
def abandon_delivery(item_id: int):
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).first()
        lock = _lock_for_task(db, item_id)
        if not item or not lock:
            raise HTTPException(status_code=409, detail="只能放弃当前锁定题目。")
        item.status = "abandoned"
        lock.target_task_id = None
        db.commit()
        return _task_to_dict(item)
