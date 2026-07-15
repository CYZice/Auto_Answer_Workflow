"""持久化的 MinerU 文件解析任务。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal
from app.models.domain import MineruJob
from app.services.mineru_v4_service import MineruApiError, MineruOptions, get_mineru_v4_service

MINERU_ROOT = Path(os.getenv("MINERU_DATA_DIR", "/app/data/mineru")).resolve()
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
ACTIVE_STATES = {"uploading", "waiting-file", "pending", "running", "converting"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _options_from_dict(value: dict[str, Any]) -> MineruOptions:
    return MineruOptions(
        model_version=str(value.get("model_version") or "vlm"),
        language=str(value.get("language") or "ch"),
        enable_formula=bool(value.get("enable_formula", True)),
        enable_table=bool(value.get("enable_table", True)),
        is_ocr=bool(value.get("is_ocr", False)),
        page_ranges=(str(value["page_ranges"]).strip() or None) if value.get("page_ranges") else None,
        extra_formats=tuple(value.get("extra_formats") or ()),
    )


def default_options(filename: str, content_type: str | None) -> MineruOptions:
    suffix = Path(filename).suffix.lower()
    return MineruOptions(is_ocr=suffix in {".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp"})


def _validate_file(filename: str, content: bytes) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise MineruApiError("不支持的文件类型；请上传 PDF、图片、Office 文档")
    if not content:
        raise MineruApiError("上传文件为空")
    if len(content) > 200 * 1024 * 1024:
        raise MineruApiError("文件超过 MinerU 的 200MB 限制")
    if suffix == ".pdf":
        try:
            source = subprocess.run(["pdfinfo", "-"], input=content, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=10)
            for line in source.stdout.decode("utf-8", errors="ignore").splitlines():
                if line.startswith("Pages:") and int(line.split(":", 1)[1].strip()) > 200:
                    raise MineruApiError("PDF 超过 MinerU 的 200 页限制")
        except FileNotFoundError:
            pass  # Docker 已提供 pdfinfo；开发环境缺失时由 MinerU 作最终页数校验。
        except subprocess.SubprocessError:
            pass


def job_to_dict(job: MineruJob, include_markdown: bool = True) -> dict[str, Any]:
    markdown = None
    if include_markdown and job.markdown_path:
        try:
            markdown = Path(job.markdown_path).read_text(encoding="utf-8")
        except OSError:
            markdown = None
    return {
        "job_id": job.job_id,
        "filename": job.filename,
        "status": job.status,
        "batch_id": job.batch_id,
        "data_id": job.data_id,
        "progress": json.loads(job.progress_json or "null"),
        "error_message": job.error_message,
        "markdown_content": markdown,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def job_images(job: MineruJob) -> dict[str, str]:
    """从已安全提取的私有结果目录读取图片，避免再次暴露或下载签名 URL。"""
    if not job.result_dir:
        return {}
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
    root = Path(job.result_dir)
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)).replace("\\", "/"): f"data:{mime[path.suffix.lower()]};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        for path in root.rglob("*") if path.is_file() and path.suffix.lower() in mime
    }


async def create_mineru_job(filename: str, content_type: str | None, content: bytes, options_input: dict[str, Any] | None = None) -> tuple[MineruJob, bool]:
    filename = Path(filename or "document.pdf").name
    _validate_file(filename, content)
    options = _options_from_dict(options_input or default_options(filename, content_type).as_dict())
    source_hash = hashlib.sha256(content).hexdigest()
    options_json = json.dumps(options.as_dict(), ensure_ascii=False, sort_keys=True)
    with SessionLocal() as db:
        existing = db.query(MineruJob).filter(MineruJob.source_sha256 == source_hash, MineruJob.options_json == options_json, MineruJob.status.in_(ACTIVE_STATES | {"done"})).order_by(MineruJob.created_at.desc()).first()
        if existing and Path(existing.source_path).exists():
            db.expunge(existing)
            return existing, True
        job_id = f"mineru_{uuid.uuid4().hex[:16]}"
        root = MINERU_ROOT / job_id
        root.mkdir(parents=True, exist_ok=False)
        source_path = root / filename
        source_path.write_bytes(content)
        job = MineruJob(job_id=job_id, filename=filename, content_type=content_type, source_path=str(source_path), source_sha256=source_hash, data_id=job_id, status="uploading", options_json=options_json)
        db.add(job)
        db.commit()
        db.refresh(job)
        db.expunge(job)
    try:
        batch_id, _ = await get_mineru_v4_service().submit_file_with_options(source_path, job_id, options)
    except Exception as exc:
        with SessionLocal() as db:
            failed = db.get(MineruJob, job_id)
            failed.status, failed.error_message = "failed", str(exc)
            db.commit()
            db.refresh(failed)
            db.expunge(failed)
            return failed, False
    with SessionLocal() as db:
        submitted = db.get(MineruJob, job_id)
        submitted.batch_id, submitted.status, submitted.next_poll_at = batch_id, "waiting-file", _utcnow()
        db.commit()
        db.refresh(submitted)
        db.expunge(submitted)
        return submitted, False


async def refresh_mineru_job(job_id: str) -> MineruJob:
    with SessionLocal() as db:
        job = db.get(MineruJob, job_id)
        if not job:
            raise MineruApiError("解析任务不存在")
        if job.status not in ACTIVE_STATES or not job.batch_id:
            db.expunge(job)
            return job
        batch_id, data_id = job.batch_id, job.data_id
    try:
        result = await get_mineru_v4_service().get_batch_result(batch_id, data_id)
        extracted = None
        if result.status == "done" and result.full_zip_url:
            extracted = await get_mineru_v4_service().extract_result_archive(result.full_zip_url, MINERU_ROOT / job_id / "result")
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(MineruJob, job_id)
            job.status, job.error_message = "failed", str(exc)
            db.commit()
            db.refresh(job)
            db.expunge(job)
            return job
    with SessionLocal() as db:
        job = db.get(MineruJob, job_id)
        job.status = result.status
        job.progress_json = json.dumps(result.extract_progress, ensure_ascii=False) if result.extract_progress else None
        job.error_message = result.error_msg
        job.next_poll_at = None if result.status in {"done", "failed"} else _utcnow() + timedelta(seconds=3)
        if extracted:
            job.result_dir, job.markdown_path = str(extracted.result_dir), str(extracted.markdown_path)
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job


async def poll_mineru_job(job_id: str, max_wait: float = 600.0) -> None:
    deadline = asyncio.get_running_loop().time() + max_wait
    while asyncio.get_running_loop().time() < deadline:
        job = await refresh_mineru_job(job_id)
        if job.status not in ACTIVE_STATES:
            return
        await asyncio.sleep(3)


async def retry_mineru_job(job_id: str) -> MineruJob:
    with SessionLocal() as db:
        job = db.get(MineruJob, job_id)
        if not job:
            raise MineruApiError("解析任务不存在")
        if job.status in ACTIVE_STATES:
            db.expunge(job)
            return job
        source_path, options = Path(job.source_path), _options_from_dict(json.loads(job.options_json))
        if not source_path.exists():
            raise MineruApiError("原始上传文件已丢失，无法重试")
        job.status, job.error_message, job.markdown_path, job.result_dir = "uploading", None, None, None
        db.commit()
    batch_id, _ = await get_mineru_v4_service().submit_file_with_options(source_path, job_id, options)
    with SessionLocal() as db:
        job = db.get(MineruJob, job_id)
        job.batch_id, job.status, job.next_poll_at = batch_id, "waiting-file", _utcnow()
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job


async def resume_pending_mineru_jobs() -> None:
    with SessionLocal() as db:
        ids = [item[0] for item in db.query(MineruJob.job_id).filter(MineruJob.status.in_(ACTIVE_STATES), MineruJob.batch_id.isnot(None)).all()]
    for job_id in ids:
        asyncio.create_task(poll_mineru_job(job_id))
