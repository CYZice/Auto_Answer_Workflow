import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.domain import AgentLog, ErrataItem, ErrataJob, Task, TaskArtifact
from app.models.schemas import ModelConfig
from app.services.errata_service import (
    ERRATA_ROOT,
    ErrataAdjudication,
    RESULT_TYPES,
    compose_errata_word_text,
    export_errata_job,
    extract_errata_items,
    generate_errata_job,
    enrich_errata_job_with_mineru,
    ensure_errata_tasks,
    item_to_dict,
    job_to_dict,
    normalize_errata_evidence,
    rebuild_errata_materials,
    review_errata_item,
    run_errata_task,
    run_errata_task_batch,
    sync_errata_task,
    _sanitize_markup,
)
from app.services.mineru_v4_service import mineru_is_configured


router = APIRouter(prefix="/api/errata", tags=["errata"])


class ErrataGenerateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    llm_config: Optional[ModelConfig] = None
    reviewer_config: Optional[ModelConfig] = None


class ErrataItemUpdateRequest(BaseModel):
    source_ref: Optional[str] = None
    question_text: Optional[str] = None
    original_answer: Optional[str] = None
    correction_opinion: Optional[str] = None
    existing_content: Optional[str] = None
    result_type: Optional[
        Literal[
            "correct",
            "partial_fix",
            "rewrite",
            "question_errata",
            "insufficient_evidence",
        ]
    ] = None
    final_text_markup: Optional[str] = None
    errata_opinion: Optional[str] = None
    status: Optional[
        Literal[
            "pending",
            "generated",
            "insufficient_evidence",
            "confirmed",
            "failed",
        ]
    ] = None
    replace_existing: Optional[bool] = None
    warnings: Optional[list[str]] = Field(default=None)


class ErrataItemCreateRequest(BaseModel):
    source_ref: str = "手动新增题目"
    question_text: str
    original_answer: str = ""
    correction_opinion: str = ""


class ErrataEvidenceRemoveRequest(BaseModel):
    evidence_path: str


class ErrataEvidenceMoveRequest(ErrataEvidenceRemoveRequest):
    target_item_id: str


@router.post("/jobs", status_code=201)
async def create_errata_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    custom_anchors: str = Form(default=""),
):
    filename = Path(file.filename or "errata.docx").name
    if Path(filename).suffix.lower() != ".docx":
        raise HTTPException(status_code=400, detail="仅支持 DOCX 文件")
    job_id = f"errata_{uuid.uuid4().hex[:12]}"
    job_dir = ERRATA_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    source_path = job_dir / filename
    source_path.write_bytes(await file.read())
    with SessionLocal() as db:
        db.add(
            ErrataJob(
                job_id=job_id,
                original_filename=filename,
                source_path=str(source_path),
                state="extracting",
                custom_anchors=custom_anchors.strip() or None,
            )
        )
        db.commit()
    try:
        await asyncio.to_thread(extract_errata_items, job_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if mineru_is_configured():
        background_tasks.add_task(
            enrich_errata_job_with_mineru,
            job_id,
        )
    with SessionLocal() as db:
        job = db.query(ErrataJob).filter(ErrataJob.job_id == job_id).first()
        count = db.query(ErrataItem).filter(ErrataItem.job_id == job_id).count()
        return job_to_dict(job, count)


@router.get("/jobs")
def list_errata_jobs():
    with SessionLocal() as db:
        jobs = db.query(ErrataJob).order_by(ErrataJob.created_at.desc()).limit(30).all()
        counts = dict(db.query(ErrataItem.job_id, func.count(ErrataItem.item_id)).group_by(ErrataItem.job_id).all())
        return {"items": [job_to_dict(job, counts.get(job.job_id, 0)) for job in jobs]}


@router.get("/jobs/{job_id}")
def get_errata_job(job_id: str):
    with SessionLocal() as db:
        job = db.query(ErrataJob).filter(ErrataJob.job_id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="勘误任务不存在")
        count = db.query(ErrataItem).filter(ErrataItem.job_id == job_id).count()
        return job_to_dict(job, count)


@router.delete("/jobs/{job_id}")
def delete_errata_job(job_id: str):
    """永久删除项目、关联 Task、节点产物和日志。"""
    with SessionLocal() as db:
        job = db.query(ErrataJob).filter(ErrataJob.job_id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="勘误任务不存在")
        task_ids = [
            task_id
            for task_id, in db.query(ErrataItem.task_id)
            .filter(ErrataItem.job_id == job_id, ErrataItem.task_id.isnot(None))
            .all()
        ]
        if task_ids:
            db.query(AgentLog).filter(AgentLog.task_id.in_(task_ids)).delete(
                synchronize_session=False
            )
            db.query(TaskArtifact).filter(TaskArtifact.task_id.in_(task_ids)).delete(
                synchronize_session=False
            )
        db.query(ErrataItem).filter(ErrataItem.job_id == job_id).delete(
            synchronize_session=False
        )
        if task_ids:
            db.query(Task).filter(Task.task_id.in_(task_ids)).delete(
                synchronize_session=False
            )
        db.delete(job)
        db.commit()
    job_dir = (ERRATA_ROOT / job_id).resolve()
    if ERRATA_ROOT in job_dir.parents and job_dir.exists():
        shutil.rmtree(job_dir)
    return {"status": "success", "deleted_task_count": len(task_ids)}


@router.post("/jobs/{job_id}/normalize-evidence")
def normalize_job_evidence(job_id: str):
    try:
        changed = normalize_errata_evidence(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success", "changed_count": changed}


@router.post("/jobs/{job_id}/rebuild-materials")
def rebuild_job_materials(job_id: str):
    try:
        rebuild_errata_materials(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success", "message": "已重建原始材料包，请重新运行勘误工作流"}


@router.get("/jobs/{job_id}/items")
def list_errata_items(job_id: str):
    with SessionLocal() as db:
        if not db.query(ErrataJob).filter(ErrataJob.job_id == job_id).first():
            raise HTTPException(status_code=404, detail="勘误任务不存在")
        items = (
            db.query(ErrataItem)
            .filter(ErrataItem.job_id == job_id)
            .order_by(ErrataItem.item_index)
            .all()
        )
        task_ids = [item.task_id for item in items if item.task_id]
        task_map = {
            task.task_id: task
            for task in db.query(Task).filter(Task.task_id.in_(task_ids)).all()
        } if task_ids else {}
        return {"items": [item_to_dict(item, task_map.get(item.task_id)) for item in items]}


@router.post("/jobs/{job_id}/generate", status_code=202)
def generate_job(
    job_id: str,
    req: ErrataGenerateRequest,
    background_tasks: BackgroundTasks,
):
    with SessionLocal() as db:
        job = db.query(ErrataJob).filter(ErrataJob.job_id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="勘误任务不存在")
        task_ids = [
            task_id
            for task_id, in db.query(ErrataItem.task_id)
            .join(Task, Task.task_id == ErrataItem.task_id)
            .filter(
                ErrataItem.job_id == job_id,
                ErrataItem.task_id.isnot(None),
                Task.state.in_(["manual", "failed", "paused", "terminated", "abandoned"]),
            )
            .all()
        ]
    background_tasks.add_task(run_errata_task_batch, task_ids)
    return {"status": "accepted", "task_count": len(task_ids)}


@router.post("/items/{item_id}/generate", status_code=202)
def generate_item(
    item_id: str,
    req: ErrataGenerateRequest,
    background_tasks: BackgroundTasks,
):
    with SessionLocal() as db:
        item = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="勘误题块不存在")
        task_id = item.task_id
        if not task_id or not db.query(Task).filter(Task.task_id == task_id).first():
            raise HTTPException(status_code=409, detail="该勘误题没有关联 Task，请重新导入或执行迁移")
    background_tasks.add_task(
        run_errata_task,
        task_id,
    )
    return {"status": "accepted"}


@router.post("/items/{item_id}/review", status_code=202)
def review_item(
    item_id: str,
    req: ErrataGenerateRequest,
    background_tasks: BackgroundTasks,
):
    with SessionLocal() as db:
        item = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="勘误题块不存在")
        task_id = item.task_id
        if not task_id:
            raise HTTPException(status_code=409, detail="该勘误题没有关联 Task")
    background_tasks.add_task(
        run_errata_task,
        task_id,
        None,
        None,
        "errata_adjudication",
        ["errata_adjudication", "word_composition"],
    )
    return {"status": "accepted"}


@router.patch("/items/{item_id}")
def update_errata_item(item_id: str, req: ErrataItemUpdateRequest):
    with SessionLocal() as db:
        item = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="勘误题块不存在")
        updates = req.model_dump(exclude_unset=True)
        context_fields = {"source_ref", "question_text", "original_answer", "correction_opinion", "existing_content"}
        context_changed = any(
            (getattr(item, field) or "") != (updates[field] or "")
            for field in context_fields & updates.keys()
        )
        for field in context_fields & updates.keys():
            setattr(item, field, updates[field] or "")
        if "result_type" in updates and updates["result_type"] not in RESULT_TYPES:
            raise HTTPException(status_code=400, detail="无效的 result_type")
        if "final_text_markup" in updates and not context_changed:
            markup = _sanitize_markup(updates["final_text_markup"] or "")
            item.final_text_markup = markup
            task = db.query(Task).filter(Task.task_id == item.task_id).first() if item.task_id else None
            if task:
                output_changed = (task.final_result or "") != markup
                task.final_result = markup or None
                task.state = "completed" if markup else "manual"
                task.current_node = "word_composition" if markup else None
                task.error_code = None
                if markup and output_changed:
                    db.add(TaskArtifact(
                        task_id=task.task_id,
                        node_name="word_composition",
                        input_revision=int(task.input_revision or 1),
                        content=markup,
                        metadata_json=json.dumps({"manual_override": True}, ensure_ascii=False),
                    ))
        if "errata_opinion" in updates:
            task = db.query(Task).filter(Task.task_id == item.task_id).first() if item.task_id else None
            if not task:
                raise HTTPException(status_code=409, detail="该勘误题没有关联 Task")
            revision = int(task.input_revision or 1)
            decision_artifact = (
                db.query(TaskArtifact)
                .filter(
                    TaskArtifact.task_id == task.task_id,
                    TaskArtifact.node_name == "errata_adjudication",
                    TaskArtifact.input_revision == revision,
                )
                .order_by(TaskArtifact.id.desc())
                .first()
            )
            solution_artifact = (
                db.query(TaskArtifact)
                .filter(
                    TaskArtifact.task_id == task.task_id,
                    TaskArtifact.node_name == "formatter",
                    TaskArtifact.input_revision == revision,
                )
                .order_by(TaskArtifact.id.desc())
                .first()
            )
            if not decision_artifact or not solution_artifact:
                raise HTTPException(status_code=409, detail="完成解题和勘误裁决后才能编辑勘误意见")
            try:
                decision = ErrataAdjudication.model_validate_json(decision_artifact.content)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="当前勘误裁决产物格式无效") from exc
            decision.errata_opinion = _sanitize_markup(updates["errata_opinion"] or "")
            decision_json = decision.model_dump_json()
            db.add(TaskArtifact(
                task_id=task.task_id,
                node_name="errata_adjudication",
                input_revision=revision,
                content=decision_json,
                metadata_json=json.dumps({"manual_override": True}, ensure_ascii=False),
            ))
            markup = _sanitize_markup(compose_errata_word_text(decision, solution_artifact.content))
            db.add(TaskArtifact(
                task_id=task.task_id,
                node_name="word_composition",
                input_revision=revision,
                content=markup,
                metadata_json=json.dumps({"manual_override": True, "deterministic": True}, ensure_ascii=False),
            ))
            item.final_text_markup = markup
            task.final_result = markup
            task.state = "completed"
            task.current_node = "word_composition"
            task.error_code = None
        if "result_type" in updates:
            item.result_type = updates["result_type"]
        if "status" in updates:
            if updates["status"] == "confirmed" and not (
                item.final_text_markup or ""
            ).strip():
                raise HTTPException(status_code=400, detail="确认前必须填写最终勘误内容")
            item.status = updates["status"]
        if "replace_existing" in updates:
            item.replace_existing = updates["replace_existing"]
        if "warnings" in updates:
            item.warnings_json = json.dumps(updates["warnings"] or [], ensure_ascii=False)
        job = db.query(ErrataJob).filter(ErrataJob.job_id == item.job_id).first()
        sync_errata_task(db, item, job, invalidate=context_changed)
        db.commit()
        db.refresh(item)
        task = db.query(Task).filter(Task.task_id == item.task_id).first() if item.task_id else None
        return item_to_dict(item, task)


@router.delete("/items/{item_id}")
def delete_errata_item(item_id: str):
    """永久删除一个勘误题及其主 Task，不影响同一 Word 项目的其他题。"""
    with SessionLocal() as db:
        item = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="勘误题块不存在")

        task_id = item.task_id
        if task_id:
            db.query(AgentLog).filter(AgentLog.task_id == task_id).delete(
                synchronize_session=False
            )
            db.query(TaskArtifact).filter(TaskArtifact.task_id == task_id).delete(
                synchronize_session=False
            )
            db.query(Task).filter(Task.task_id == task_id).delete(
                synchronize_session=False
            )
        db.delete(item)
        db.commit()
        return {"status": "success", "message": "勘误题已永久删除"}


@router.post("/jobs/{job_id}/items", status_code=201)
def create_manual_errata_item(job_id: str, req: ErrataItemCreateRequest):
    with SessionLocal() as db:
        if not db.query(ErrataJob).filter(ErrataJob.job_id == job_id).first():
            raise HTTPException(status_code=404, detail="勘误任务不存在")
        next_index = (db.query(func.max(ErrataItem.item_index)).filter(ErrataItem.job_id == job_id).scalar() or 0) + 1
        item = ErrataItem(
            item_id=f"{job_id}_manual_{uuid.uuid4().hex[:8]}", job_id=job_id, item_index=next_index,
            source_ref=req.source_ref, question_text=req.question_text, original_answer=req.original_answer,
            correction_opinion=req.correction_opinion,
            material_text="\n".join(value for value in (req.question_text, req.original_answer, req.correction_opinion) if value),
            material_version=1,
            status="pending",
        )
        db.add(item); db.flush(); ensure_errata_tasks(db, job_id, create_missing=True); db.commit(); db.refresh(item)
        task = db.query(Task).filter(Task.task_id == item.task_id).first()
        return item_to_dict(item, task)


@router.post("/items/{item_id}/evidence")
async def add_errata_evidence(item_id: str, file: UploadFile = File(...)):
    suffix = Path(file.filename or "evidence.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="仅支持 PNG、JPG、WEBP 图片")
    with SessionLocal() as db:
        item = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="勘误题块不存在")
        folder = ERRATA_ROOT / item.job_id / "evidence"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"manual_{uuid.uuid4().hex[:10]}{suffix}"
        (folder / filename).write_bytes(await file.read())
        evidence = json.loads(item.evidence_json or "[]")
        evidence.append(f"evidence/{filename}")
        item.evidence_json = json.dumps(evidence, ensure_ascii=False)
        sync_errata_task(db, item, create_missing=False, invalidate=True)
        db.commit()
        db.refresh(item)
        task = db.query(Task).filter(Task.task_id == item.task_id).first() if item.task_id else None
        return item_to_dict(item, task)


@router.delete("/items/{item_id}/evidence")
def remove_errata_evidence(item_id: str, req: ErrataEvidenceRemoveRequest):
    """只解除当前题的证据关联，不删除可能被其他题共用的文件。"""
    with SessionLocal() as db:
        item = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="勘误题块不存在")
        evidence = json.loads(item.evidence_json or "[]")
        if req.evidence_path not in evidence:
            raise HTTPException(status_code=404, detail="该图片不属于当前勘误题")
        evidence.remove(req.evidence_path)
        item.evidence_json = json.dumps(evidence, ensure_ascii=False)
        item.review_status = "pending"
        if item.status == "confirmed":
            item.status = "pending"
        sync_errata_task(db, item, create_missing=False, invalidate=True)
        db.commit()
        db.refresh(item)
        task = db.query(Task).filter(Task.task_id == item.task_id).first() if item.task_id else None
        return item_to_dict(item, task)


@router.post("/items/{item_id}/evidence/move")
def move_errata_evidence(item_id: str, req: ErrataEvidenceMoveRequest):
    with SessionLocal() as db:
        source = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).first()
        target = db.query(ErrataItem).filter(ErrataItem.item_id == req.target_item_id).first()
        if not source or not target:
            raise HTTPException(status_code=404, detail="勘误题块不存在")
        if source.item_id == target.item_id:
            raise HTTPException(status_code=400, detail="不能移动到当前题")
        if source.job_id != target.job_id:
            raise HTTPException(status_code=400, detail="图片只能移动到同一勘误项目中的题目")
        source_evidence = json.loads(source.evidence_json or "[]")
        if req.evidence_path not in source_evidence:
            raise HTTPException(status_code=404, detail="该图片不属于当前勘误题")
        source_evidence.remove(req.evidence_path)
        target_evidence = json.loads(target.evidence_json or "[]")
        if req.evidence_path not in target_evidence:
            target_evidence.append(req.evidence_path)
        source.evidence_json = json.dumps(source_evidence, ensure_ascii=False)
        target.evidence_json = json.dumps(target_evidence, ensure_ascii=False)
        for item in (source, target):
            item.review_status = "pending"
            if item.status == "confirmed":
                item.status = "pending"
        sync_errata_task(db, source, create_missing=False, invalidate=True)
        sync_errata_task(db, target, create_missing=False, invalidate=True)
        db.commit()
        db.refresh(source)
        db.refresh(target)
        source_task = db.query(Task).filter(Task.task_id == source.task_id).first() if source.task_id else None
        target_task = db.query(Task).filter(Task.task_id == target.task_id).first() if target.task_id else None
        return {"source_item": item_to_dict(source, source_task), "target_item": item_to_dict(target, target_task)}


@router.get("/jobs/{job_id}/evidence/{relative_path:path}")
def get_errata_evidence(job_id: str, relative_path: str):
    path = (ERRATA_ROOT / job_id / relative_path).resolve()
    if ERRATA_ROOT not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="证据图片不存在")
    return FileResponse(path)


@router.post("/jobs/{job_id}/export")
def export_job(job_id: str):
    try:
        output_path = export_errata_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=output_path.name,
    )
