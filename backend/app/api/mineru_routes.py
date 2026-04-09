"""
MinerU API 路由

提供试卷智能解析的 API 接口（使用 v4 精准解析 API）
"""
import asyncio
import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from fastapi import APIRouter, BackgroundTasks, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.models.domain import Task
from app.models.schemas import TaskStatus
from app.services.mineru_v4_service import get_mineru_v4_service, MineruV4Service

router = APIRouter(prefix="/api/mineru", tags=["mineru"])

# v4 API 配置
MINERU_V4_API_BASE = "https://mineru.net/api/v4"


# === 请求/响应模型 ===

class MineruParseUrlRequest(BaseModel):
    """URL 解析请求"""
    url: str = Field(description="试卷图片或 PDF 的 URL")
    model_version: Optional[str] = Field(default="vlm", description="模型版本")


class MineruParseResponse(BaseModel):
    """解析任务创建响应"""
    mineru_task_id: str
    status: str
    created_at: datetime


class MineruParseResultResponse(BaseModel):
    """解析结果查询响应"""
    mineru_task_id: str
    status: str
    markdown_url: Optional[str] = None
    markdown_content: Optional[str] = None
    error_message: Optional[str] = None
    extract_progress: Optional[dict] = None


class MineruUploadUrlResponse(BaseModel):
    """获取上传 URL 响应"""
    batch_id: str
    upload_url: str


# === 试卷解题请求/响应 ===

class PaperSolveRequest(BaseModel):
    """试卷解题请求"""
    original_images: Optional[list[str]] = Field(default=None, description="原图 Base64 列表")
    paper_title: Optional[str] = Field(default="", description="试卷标题")
    paper_subject: Optional[str] = Field(default="", description="试卷科目")


class PaperSolveResponse(BaseModel):
    """试卷解题响应"""
    paper_task_id: str
    question_count: int
    status: str
    message: str
    task_ids: list[str] = []
    thread_id: str = ""


# === 辅助函数 ===

def get_v4_service() -> MineruV4Service:
    """获取 MinerU v4 服务实例"""
    return get_mineru_v4_service()


async def download_and_extract_markdown(zip_url: str) -> str:
    """下载 zip 文件并提取 markdown 内容"""
    resp = requests.get(zip_url, timeout=120)
    resp.raise_for_status()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "result.zip")
        with open(zip_path, "wb") as f:
            f.write(resp.content)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)

        # 查找 markdown 文件
        for root, _, files in os.walk(tmpdir):
            for fname in files:
                if fname.endswith(".md"):
                    fpath = os.path.join(root, fname)
                    with open(fpath, "r", encoding="utf-8") as f:
                        return f.read()

    raise ValueError("zip 中未找到 markdown 文件")


def _create_question_task(
    question_num: int,
    question_content: str,
    question_type: str,
    image_urls: list[str],
    thread_id: str,
    background_tasks: BackgroundTasks,
) -> str:
    """
    为单道题创建任务并启动工作流

    复用现有 create_task 逻辑，确保：
    - 任务在主界面可见
    - 数据库存储格式一致
    - 工作流执行相同
    """
    from app.main import run_agent_workflow_async

    task_id = f"q_{question_num}_{uuid.uuid4().hex[:8]}"

    history_data = {
        "image_urls": image_urls,
        "question_number": question_num,
        "question_type": question_type,
        "question_content": question_content,
        "solver_config": {},
        "reviewer_config": {},
        "formatter_config": {},
    }

    new_task = Task(
        task_id=task_id,
        thread_id=thread_id,
        image_url=image_urls[0] if image_urls else "",
        state=TaskStatus.QUEUED.value,
        history=json.dumps(history_data, ensure_ascii=False),
    )

    with SessionLocal() as db:
        db.add(new_task)
        db.commit()

    # 启动工作流（与手动解题完全相同）
    background_tasks.add_task(
        run_agent_workflow_async,
        task_id,
        "solver",
        None,
    )

    return task_id


# === 路由实现 ===

@router.post("/parse/file", response_model=MineruUploadUrlResponse)
async def get_upload_url(
    file: UploadFile = File(..., description="试卷图片或 PDF 文件"),
):
    """获取文件上传 URL"""
    service = get_v4_service()

    batch_url = f"{MINERU_V4_API_BASE}/file-urls/batch"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {service.api_token}",
    }
    data = {
        "files": [{"name": file.filename or "document.pdf", "data_id": "upload"}],
        "model_version": "vlm",
    }

    resp = requests.post(batch_url, headers=headers, json=data, timeout=30)
    result = resp.json()

    if result.get("code") != 0:
        raise HTTPException(status_code=500, detail=f"获取上传链接失败: {result.get('msg')}")

    batch_id = result["data"]["batch_id"]
    upload_url = result["data"]["file_urls"][0]

    return MineruUploadUrlResponse(batch_id=batch_id, upload_url=upload_url)


@router.post("/parse/{batch_id}/upload", response_model=MineruParseResponse)
async def upload_file(
    batch_id: str,
    file: UploadFile = File(..., description="试卷图片或 PDF 文件"),
):
    """上传文件并提交解析任务"""
    service = get_v4_service()

    file_content = await file.read()

    batch_url = f"{MINERU_V4_API_BASE}/file-urls/batch"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {service.api_token}",
    }
    data = {
        "files": [{"name": file.filename or "document.pdf", "data_id": batch_id}],
        "model_version": "vlm",
    }

    resp = requests.post(batch_url, headers=headers, json=data, timeout=30)
    result = resp.json()

    if result.get("code") != 0:
        raise HTTPException(status_code=500, detail=f"获取上传链接失败: {result.get('msg')}")

    upload_url = result["data"]["file_urls"][0]

    upload_resp = requests.put(upload_url, data=file_content, timeout=60)
    if upload_resp.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail=f"文件上传失败, HTTP {upload_resp.status_code}")

    # 轮询等待解析完成
    result_url = f"{MINERU_V4_API_BASE}/extract-results/batch/{batch_id}"

    import time
    start = time.time()
    max_wait = 600.0

    while time.time() - start < max_wait:
        resp = requests.get(result_url, headers=headers, timeout=30)
        result = resp.json()

        if result.get("code") == 0:
            extract_result = result["data"]["extract_result"][0]
            state = extract_result["state"]

            if state == "done":
                return MineruParseResponse(
                    mineru_task_id=batch_id,
                    status="done",
                    created_at=datetime.now(),
                )

            if state == "failed":
                raise HTTPException(status_code=500, detail=f"解析失败: {extract_result.get('err_msg')}")

        time.sleep(3)

    raise HTTPException(status_code=500, detail="解析超时")


@router.post("/parse/url", response_model=MineruParseResponse)
async def parse_url(request: MineruParseUrlRequest):
    """URL 解析"""
    service = get_v4_service()

    try:
        mineru_task_id = await service.parse_url(
            url=request.url,
            model_version=request.model_version or "vlm",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MineruParseResponse(
        mineru_task_id=mineru_task_id,
        status="pending",
        created_at=datetime.now(),
    )


@router.get("/parse/{task_id}", response_model=MineruParseResultResponse)
async def get_parse_result(task_id: str):
    """查询解析结果"""
    service = get_v4_service()

    try:
        result = await service.get_result(task_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MineruParseResultResponse(
        mineru_task_id=result.task_id,
        status=result.status,
        markdown_url=result.full_zip_url,
        error_message=result.error_msg,
        extract_progress=result.extract_progress,
    )


@router.post("/parse/{task_id}/wait", response_model=MineruParseResultResponse)
async def wait_parse_completion(
    task_id: str,
    poll_interval: Optional[float] = Query(default=3.0),
    max_wait: Optional[float] = Query(default=600.0),
):
    """等待解析完成"""
    service = get_v4_service()

    try:
        result = await service.wait_for_completion(
            task_id=task_id,
            poll_interval=poll_interval,
            max_wait=max_wait,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    markdown_content = None
    if result.status == "done" and result.full_zip_url:
        try:
            markdown_content = await download_and_extract_markdown(result.full_zip_url)
        except Exception:
            pass

    return MineruParseResultResponse(
        mineru_task_id=result.task_id,
        status=result.status,
        markdown_url=result.full_zip_url,
        markdown_content=markdown_content,
        error_message=result.error_msg,
        extract_progress=result.extract_progress,
    )


# === 试卷解题与导出 ===

@router.post("/paper/{mineru_task_id}/solve", response_model=PaperSolveResponse)
async def solve_paper(
    mineru_task_id: str,
    request: PaperSolveRequest,
    background_tasks: BackgroundTasks,
):
    """
    解析试卷并自动开始解题

    流程：
    1. 获取 MinerU 解析结果
    2. 解析出每道题
    3. 为每道题创建独立任务（复用现有工作流）
    4. 用户在主界面可看到解题过程（与手动解题完全相同）
    """
    service = get_v4_service()

    # 获取 MinerU 解析结果
    result = await service.wait_for_completion(mineru_task_id, max_wait=600.0)

    if result.status != "done":
        raise HTTPException(status_code=400, detail=f"MinerU 解析未完成，当前状态: {result.status}")

    # 获取 markdown 内容
    markdown = result.markdown_content
    if not markdown and result.full_zip_url:
        markdown = await download_and_extract_markdown(result.full_zip_url)

    if not markdown:
        raise HTTPException(status_code=500, detail="无法获取 Markdown 内容")

    # 解析题目
    from app.services.markdown_parser import MarkdownParser
    parser = MarkdownParser()
    questions = parser.parse(markdown)

    # 关联原图
    original_images = request.original_images or []
    questions_with_images = parser.associate_images(questions, original_images)

    # 创建试卷解题的线程 ID
    paper_task_id = f"paper_{mineru_task_id}"
    thread_id = f"thread_{uuid.uuid4().hex[:8]}"

    # 为每道题创建任务（复用现有工作流）
    task_ids = []
    for qwi in questions_with_images:
        task_id = _create_question_task(
            question_num=qwi.question.number,
            question_content=qwi.question.content,
            question_type=qwi.question.question_type,
            image_urls=qwi.original_images,
            thread_id=thread_id,
            background_tasks=background_tasks,
        )
        task_ids.append(task_id)

    return PaperSolveResponse(
        paper_task_id=paper_task_id,
        question_count=len(task_ids),
        status="initiated",
        message=f"试卷解析完成，共 {len(task_ids)} 道题目，解题流程已启动",
        task_ids=task_ids,
        thread_id=thread_id,
    )


@router.get("/paper/{mineru_task_id}/status")
async def get_paper_solve_status(mineru_task_id: str):
    """
    查询试卷解题进度

    通过 thread_id 查找所有子任务
    """
    paper_task_id = f"paper_{mineru_task_id}"

    # 查找相关的所有任务
    with SessionLocal() as db:
        tasks = db.query(Task).filter(
            Task.thread_id.like(f"%{mineru_task_id}%")
        ).all()

    if not tasks:
        tasks = db.query(Task).filter(
            Task.task_id.like(f"%{mineru_task_id}%")
        ).all()

    results = []
    completed = 0
    for t in tasks:
        history_data = json.loads(t.history or "{}")
        question_num = history_data.get("question_number", 0)
        question_type = history_data.get("question_type", "未分类")

        status = t.state or "unknown"
        if status == "completed":
            completed += 1

        results.append({
            "task_id": t.task_id,
            "number": question_num,
            "type": question_type,
            "status": status,
            "final_result": t.final_result,
        })

    # 按题号排序
    results.sort(key=lambda x: x["number"])

    return {
        "paper_task_id": paper_task_id,
        "total": len(results),
        "completed": completed,
        "results": results,
    }


@router.get("/paper/{mineru_task_id}/export/docx")
async def export_paper_docx(
    mineru_task_id: str,
    paper_title: str = Query(default=""),
    paper_subject: str = Query(default=""),
):
    """
    导出试卷为 DOCX

    复用现有的 build_split_export_markdown 函数
    按原卷题型顺序和题号顺序排版
    """
    from app.main import build_split_export_markdown

    # 查找所有子任务
    with SessionLocal() as db:
        tasks = db.query(Task).filter(
            Task.thread_id.like(f"%{mineru_task_id}%")
        ).all()

    if not tasks:
        raise HTTPException(status_code=404, detail="未找到解题任务")

    # 收集结果
    task_results = []
    for t in tasks:
        history_data = json.loads(t.history or "{}")
        question_num = history_data.get("question_number", 0)
        question_type = history_data.get("question_type", "未分类")

        task_results.append({
            "number": question_num,
            "type": question_type,
            "question": history_data.get("question_content", ""),
            "answer": t.final_result or "",
        })

    # 按题型和题号排序
    task_results.sort(key=lambda x: (x["type"], x["number"]))

    # 按题型分组，items 格式符合 build_split_export_markdown 要求
    grouped = {}
    for item in task_results:
        qtype = item["type"]
        if qtype not in grouped:
            grouped[qtype] = []
        # 构建符合 build_split_export_markdown 要求的 items 格式
        grouped[qtype].append({
            "question": item["question"],
            "answer": item["answer"],
            "only_question": not bool(item["answer"]),
        })

    groups = [{"group_name": k, "items": v} for k, v in grouped.items()]

    # 生成 Markdown
    markdown_content = build_split_export_markdown(
        groups,
        paper_subject=paper_subject.strip(),
        paper_title=paper_title.strip(),
    )

    # 转换为 DOCX
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
        docx_path = f.name

    md_path = tempfile.mktemp(suffix='.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

    try:
        import subprocess
        subprocess.run(
            ["pandoc", "-f", "markdown+hard_line_breaks", "-t", "docx", "-o", docx_path, md_path],
            check=True,
            capture_output=True,
        )
    finally:
        Path(md_path).unlink(missing_ok=True)

    filename = f"{paper_subject or '试卷'}_{paper_title or '解析结果'}.docx"
    return FileResponse(
        path=docx_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/paper/{mineru_task_id}/questions")
async def get_paper_questions(mineru_task_id: str):
    """获取试卷题目列表"""
    service = get_v4_service()

    result = await service.wait_for_completion(mineru_task_id, max_wait=600.0)

    if result.status != "done":
        raise HTTPException(status_code=400, detail=f"MinerU 解析未完成，当前状态: {result.status}")

    markdown = result.markdown_content
    if not markdown and result.full_zip_url:
        markdown = await download_and_extract_markdown(result.full_zip_url)

    if not markdown:
        raise HTTPException(status_code=500, detail="无法获取 Markdown 内容")

    from app.services.markdown_parser import MarkdownParser
    parser = MarkdownParser()
    questions = parser.parse(markdown)

    grouped = parser.split_by_type(questions)

    return {
        "total": len(questions),
        "questions": [
            {
                "number": q.number,
                "type": q.question_type,
                "content": q.content,
                "images": q.images,
            }
            for q in questions
        ],
        "grouped": {
            qtype: [{"number": q.number, "content": q.content[:100]} for q in qs]
            for qtype, qs in grouped.items()
        },
    }
