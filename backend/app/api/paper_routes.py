import base64
import json
import mimetypes
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.domain import PaperProject, PaperQuestion, Task
from app.services.paper_docx_service import (
    PAPER_ROOT,
    export_paper_answers,
    apply_mineru_paper_questions,
    enrich_paper_project_with_mineru,
    extract_paper_questions,
    project_to_dict,
    question_to_dict,
)
from app.services.mineru_v4_service import mineru_is_configured


router = APIRouter(prefix="/api/papers", tags=["papers"])


class PaperQuestionUpdate(BaseModel):
    group_name: str | None = None
    question_number: str | None = None
    question_text: str | None = None
    enabled: bool | None = None


class PaperSolveRequest(BaseModel):
    skip_review: bool = False


def _data_url(path: str) -> str:
    file_path = Path(path)
    mime = mimetypes.guess_type(file_path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(file_path.read_bytes()).decode('ascii')}"


@router.post("/docx", status_code=201)
async def create_paper(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    filename = Path(file.filename or "paper.docx").name
    if Path(filename).suffix.lower() != ".docx":
        raise HTTPException(status_code=400, detail="DOCX 输入应直接上传；图片或 PDF 请使用智能解析入口")
    paper_id = f"paper_{uuid.uuid4().hex[:12]}"
    project_dir = PAPER_ROOT / paper_id
    project_dir.mkdir(parents=True, exist_ok=False)
    source_path = project_dir / filename
    source_path.write_bytes(await file.read())
    with SessionLocal() as db:
        db.add(PaperProject(paper_id=paper_id, original_filename=filename, source_path=str(source_path), state="extracting"))
        db.commit()
    try:
        extract_paper_questions(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if mineru_is_configured():
        background_tasks.add_task(
            enrich_paper_project_with_mineru,
            paper_id,
        )
    return get_paper(paper_id)


@router.get("")
def list_papers():
    with SessionLocal() as db:
        projects = db.query(PaperProject).order_by(PaperProject.created_at.desc()).limit(30).all()
        counts = dict(db.query(PaperQuestion.paper_id, func.count(PaperQuestion.id)).group_by(PaperQuestion.paper_id).all())
        return {"items": [{
            "paper_id": project.paper_id,
            "original_filename": project.original_filename,
            "state": project.state,
            "mineru_status": project.mineru_status,
            "question_count": counts.get(project.paper_id, 0),
            "updated_at": project.updated_at,
        } for project in projects]}


@router.get("/{paper_id}")
def get_paper(paper_id: str):
    with SessionLocal() as db:
        project = db.query(PaperProject).filter(PaperProject.paper_id == paper_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="试卷项目不存在")
        items = db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).order_by(PaperQuestion.item_index).all()
        task_ids = [item.task_id for item in items if item.task_id]
        tasks = db.query(Task).filter(Task.task_id.in_(task_ids)).all() if task_ids else []
        task_map = {task.task_id: task for task in tasks}
        questions = [question_to_dict(item, task_map.get(item.task_id)) for item in items]
        running = any(item["state"] in {"queued", "solving", "reviewing", "formatting"} for item in questions)
        if running and project.state != "solving":
            project.state = "solving"
            db.commit()
        return project_to_dict(project, questions)


@router.patch("/questions/{question_id}")
def update_question(question_id: int, req: PaperQuestionUpdate):
    with SessionLocal() as db:
        item = db.query(PaperQuestion).filter(PaperQuestion.id == question_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="题目不存在")
        for key, value in req.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        item.state = "pending"
        db.commit()
        db.refresh(item)
        return question_to_dict(item)


@router.post("/{paper_id}/apply-mineru")
def apply_mineru_questions(paper_id: str):
    try:
        items = apply_mineru_paper_questions(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"questions": [question_to_dict(item) for item in items]}


@router.get("/{paper_id}/images/{filename}")
def get_question_image(paper_id: str, filename: str):
    path = (PAPER_ROOT / paper_id / "media" / Path(filename).name).resolve()
    if PAPER_ROOT not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path)


@router.post("/{paper_id}/solve", status_code=202)
def solve_paper(paper_id: str, req: PaperSolveRequest, background_tasks: BackgroundTasks):
    from app.main import run_agent_workflow_async

    created: list[str] = []
    with SessionLocal() as db:
        project = db.query(PaperProject).filter(PaperProject.paper_id == paper_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="试卷项目不存在")
        items = db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id, PaperQuestion.enabled.is_(True)).order_by(PaperQuestion.item_index).all()
        for item in items:
            if item.task_id:
                task = db.query(Task).filter(Task.task_id == item.task_id).first()
                if task and task.state in {"queued", "solving", "reviewing", "formatting", "completed"}:
                    continue
            image_paths = json.loads(item.image_paths_json or "[]")
            image_urls = [_data_url(path) for path in image_paths if Path(path).exists()]
            task_id = str(uuid.uuid4())
            target_nodes = ["solver", "formatter"] if req.skip_review else ["solver", "reviewer", "formatter"]
            history = {
                "workflow_type": "paper",
                "question_text": item.question_text,
                "image_urls": image_urls,
                "target_nodes": target_nodes,
                "paper_id": paper_id,
                "paper_question_id": item.id,
                "source_kind": "paper",
                "source_id": paper_id,
                "source_title": project.original_filename,
                "source_item_label": item.stable_key,
            }
            db.add(Task(task_id=task_id, thread_id=task_id, image_url=image_urls[0] if image_urls else "", image_urls_json=json.dumps(image_urls, ensure_ascii=False), question_text=item.question_text, input_revision=1, workflow_type="full_paper", source_kind="paper", source_id=paper_id, source_item_id=str(item.id), state="queued", retry_count=0, history=json.dumps(history, ensure_ascii=False)))
            item.task_id = task_id
            item.state = "queued"
            created.append(task_id)
        project.state = "solving"
        db.commit()
    for task_id in created:
        background_tasks.add_task(run_agent_workflow_async, task_id, "solver", ["solver", "formatter"] if req.skip_review else ["solver", "reviewer", "formatter"])
    return {"status": "accepted", "created_task_ids": created}


@router.post("/{paper_id}/export")
def export_paper(paper_id: str):
    try:
        output_path = export_paper_answers(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(output_path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=output_path.name)
