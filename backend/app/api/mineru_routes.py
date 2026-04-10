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
from app.models.schemas import ModelConfig, TaskStatus
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

    class QuestionOverrideItem(BaseModel):
        number: int = Field(ge=1, description="题号")
        type: str = Field(description="题型")
        content: str = Field(description="题干文本")
        images: Optional[list[str]] = Field(
            default=None, description="题目关联图片 URL（可选）"
        )

    original_images: Optional[list[str]] = Field(
        default=None, description="原图 Base64 列表"
    )
    paper_title: Optional[str] = Field(default="", description="试卷标题")
    paper_subject: Optional[str] = Field(default="", description="试卷科目")
    solver_config: Optional[ModelConfig] = Field(
        default=None, description="Solver 节点模型配置"
    )
    reviewer_config: Optional[ModelConfig] = Field(
        default=None, description="Reviewer 节点模型配置"
    )
    formatter_config: Optional[ModelConfig] = Field(
        default=None, description="Formatter 节点模型配置"
    )
    workflow_template_id: Optional[str] = Field(
        default=None, description="本次解题使用的提示词模板 ID"
    )
    questions_override: Optional[list[QuestionOverrideItem]] = Field(
        default=None,
        description="人工修订后的题目列表（可选，若提供则优先用于解题）",
    )


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


def _create_question_task(
    question_num: int,
    question_content: str,
    question_type: str,
    image_urls: list[str],
    thread_id: str,
    background_tasks: BackgroundTasks,
    solver_config: Optional[dict] = None,
    reviewer_config: Optional[dict] = None,
    formatter_config: Optional[dict] = None,
    workflow_template_id: Optional[str] = None,
) -> str:
    """
    为单道题创建任务并启动工作流

    复用现有 create_task 逻辑，确保：
    - 任务在主界面可见
    - 数据库存储格式一致
    - 工作流执行相同
    """
    from app.main import run_agent_workflow_async
    from app.services.runtime_config import read_runtime_settings

    task_id = f"q_{question_num}_{uuid.uuid4().hex[:8]}"

    # 获取当前激活的模板 ID
    runtime_settings = read_runtime_settings()
    active_template_id = workflow_template_id or runtime_settings.get(
        "active_template_id"
    )

    history_data = {
        "image_urls": image_urls,
        "question_number": question_num,
        "question_type": question_type,
        "question_content": question_content,
        "workflow_template_id": active_template_id,
        "solver_config": dict(solver_config or {}),
        "reviewer_config": dict(reviewer_config or {}),
        "formatter_config": dict(formatter_config or {}),
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


@router.post("/parse/file", response_model=MineruParseResponse)
async def parse_file(
    file: UploadFile = File(..., description="试卷图片或 PDF 文件"),
):
    """
    后端代理上传文件到 OSS（解决浏览器跨域问题），返回 batch_id 供后续轮询
    """
    service = get_v4_service()

    file_content = await file.read()

    # 1. 获取上传 URL
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
        raise HTTPException(
            status_code=500, detail=f"获取上传链接失败: {result.get('msg')}"
        )

    batch_id = result["data"]["batch_id"]
    upload_url = result["data"]["file_urls"][0]

    # 2. 后端代理上传到 OSS（避免浏览器跨域限制）
    upload_resp = requests.put(upload_url, data=file_content, timeout=120)
    if upload_resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=500, detail=f"文件上传失败, HTTP {upload_resp.status_code}"
        )

    # 3. 返回 batch_id，前端轮询 /parse/{batch_id} 获取结果
    return MineruParseResponse(
        mineru_task_id=batch_id,
        status="pending",
        created_at=datetime.now(),
    )


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
        raise HTTPException(
            status_code=500, detail=f"获取上传链接失败: {result.get('msg')}"
        )

    upload_url = result["data"]["file_urls"][0]

    upload_resp = requests.put(upload_url, data=file_content, timeout=60)
    if upload_resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=500, detail=f"文件上传失败, HTTP {upload_resp.status_code}"
        )

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
                raise HTTPException(
                    status_code=500, detail=f"解析失败: {extract_result.get('err_msg')}"
                )

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
    """查询解析结果（批量文件方式）"""
    service = get_v4_service()

    try:
        result = await service.get_batch_result(task_id)
        print(f"[DEBUG get_parse_result] task_id={task_id}, result.status={result.status}, result.full_zip_url={result.full_zip_url}, result.extract_progress={result.extract_progress}")
    except Exception as e:
        print(f"[DEBUG get_parse_result] error: {e}")
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
    """等待解析完成（批量文件方式）"""
    service = get_v4_service()

    try:
        result = await service.wait_for_batch_completion(
            batch_id=task_id,
            poll_interval=poll_interval,
            max_wait=max_wait,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    markdown_content = None
    if result.status == "done" and result.full_zip_url:
        try:
            markdown_content = await service.download_and_extract_markdown(
                result.full_zip_url
            )
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
    result = await service.wait_for_batch_completion(mineru_task_id, max_wait=600.0)

    if result.status != "done":
        raise HTTPException(
            status_code=400, detail=f"MinerU 解析未完成，当前状态: {result.status}"
        )

    # 获取 markdown 内容
    markdown = result.markdown_content
    images_base64: dict[str, str] = {}  # {relative_path: base64_data_url}

    if not markdown and result.full_zip_url:
        markdown = await service.download_and_extract_markdown(result.full_zip_url)

    if not markdown:
        raise HTTPException(status_code=500, detail="无法获取 Markdown 内容")

    # 从 zip 包中提取图片并转换为 base64
    if result.full_zip_url:
        try:
            images_base64 = await service.download_and_extract_images(result.full_zip_url)
            print(f"[solve_paper] 提取了 {len(images_base64)} 张图片为 base64")

            # 将 markdown 中的相对路径图片替换为 base64 data URL
            if images_base64:
                for relative_path, data_url in images_base64.items():
                    # 替换 ![...](relative_path) 为 ![...](data_url)
                    markdown = markdown.replace(f"({relative_path})", f"({data_url})")
        except Exception as e:
            print(f"[solve_paper] 提取图片失败: {e}")
            images_base64 = {}

    # 解析题目
    from app.services.markdown_parser import MarkdownParser

    parser = MarkdownParser()
    parsed_questions = parser.parse(markdown)

    # 若前端传入人工修订题目，优先使用修订结果。
    questions_override = request.questions_override or []
    if questions_override:
        from app.services.markdown_parser import Question

        # 获取所有 base64 图片列表（用于 fallback）
        all_base64_images = list(images_base64.values()) if images_base64 else []
        base64_idx = 0  # 用于按顺序分配 base64 图片

        seen_numbers: set[int] = set()
        questions: list[Question] = []
        for item in questions_override:
            q_number = int(item.number)
            q_type = (item.type or "").strip()
            q_content = (item.content or "").strip()

            # 将相对路径图片替换为 base64 data URL
            q_images = []
            has_any_image = False
            for img in (item.images or []):
                if not isinstance(img, str) or not img.strip():
                    continue
                img = img.strip()
                has_any_image = True
                # 如果是相对路径（如 images/xxx.jpg），替换为 base64
                if images_base64 and img in images_base64:
                    q_images.append(images_base64[img])
                else:
                    q_images.append(img)

            # 如果没有提供任何图片，使用 base64 fallback（按顺序分配）
            if not has_any_image and all_base64_images:
                q_images = [all_base64_images[base64_idx % len(all_base64_images)]]
                base64_idx += 1

            if q_number < 1:
                raise HTTPException(
                    status_code=400,
                    detail="questions_override 中题号必须大于等于 1",
                )
            if q_number in seen_numbers:
                raise HTTPException(
                    status_code=400,
                    detail=f"questions_override 中存在重复题号: {q_number}",
                )
            if not q_type:
                raise HTTPException(
                    status_code=400,
                    detail=f"questions_override 第 {q_number} 题题型不能为空",
                )
            if not q_content:
                raise HTTPException(
                    status_code=400,
                    detail=f"questions_override 第 {q_number} 题题干不能为空",
                )

            seen_numbers.add(q_number)
            questions.append(
                Question(
                    number=q_number,
                    question_type=q_type,
                    content=q_content,
                    images=q_images,
                )
            )
    else:
        questions = parsed_questions

    # 关联原图（仅当没有可用图片时使用）
    original_images = request.original_images or []
    questions_with_images = parser.associate_images(questions, original_images)
    solver_config = request.solver_config.model_dump() if request.solver_config else {}
    reviewer_config = (
        request.reviewer_config.model_dump() if request.reviewer_config else {}
    )
    formatter_config = (
        request.formatter_config.model_dump() if request.formatter_config else {}
    )

    # 创建试卷解题的线程 ID（包含 mineru_task_id 以便后续导出查询）
    paper_task_id = f"paper_{mineru_task_id}"
    thread_id = f"thread_{mineru_task_id}"

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
            solver_config=solver_config,
            reviewer_config=reviewer_config,
            formatter_config=formatter_config,
            workflow_template_id=request.workflow_template_id,
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
        tasks = db.query(Task).filter(Task.thread_id.like(f"%{mineru_task_id}%")).all()

    if not tasks:
        tasks = db.query(Task).filter(Task.task_id.like(f"%{mineru_task_id}%")).all()

    results = []
    completed = 0
    for t in tasks:
        history_data = json.loads(t.history or "{}")
        question_num = history_data.get("question_number", 0)
        question_type = history_data.get("question_type", "未分类")

        status = t.state or "unknown"
        if status == "completed":
            completed += 1

        results.append(
            {
                "task_id": t.task_id,
                "number": question_num,
                "type": question_type,
                "status": status,
                "final_result": t.final_result,
            }
        )

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
        tasks = db.query(Task).filter(Task.thread_id.like(f"%{mineru_task_id}%")).all()

    if not tasks:
        raise HTTPException(status_code=404, detail="未找到解题任务")

    # 收集结果
    task_results = []
    for t in tasks:
        history_data = json.loads(t.history or "{}")
        question_num = history_data.get("question_number", 0)
        question_type = history_data.get("question_type", "未分类")

        # 使用 question_preview 和 answer_preview（从 final_result 解析出来的）
        question_text = (t.question_preview or "").strip()
        answer_text = (t.answer_preview or "").strip()

        # 嵌入原卷题图
        image_urls = history_data.get("image_urls") or []
        for img_url in image_urls:
            if img_url.startswith("data:") or img_url.startswith("http"):
                question_text += f"\n\n![题{question_num}]({img_url})"

        task_results.append(
            {
                "number": question_num,
                "type": question_type,
                "question": question_text,
                "answer": answer_text,
                "only_question": not bool(answer_text),
            }
        )

    # 按题型和题号排序
    task_results.sort(key=lambda x: (x["type"], x["number"]))

    # 按题型分组，items 格式符合 build_split_export_markdown 要求
    grouped = {}
    for item in task_results:
        qtype = item["type"]
        if qtype not in grouped:
            grouped[qtype] = []
        # 构建符合 build_split_export_markdown 要求的 items 格式
        grouped[qtype].append(
            {
                "question": item["question"],
                "answer": item["answer"],
                "only_question": item.get("only_question", False),
            }
        )

    groups = [{"group_name": k, "items": v} for k, v in grouped.items()]

    # 生成 Markdown
    markdown_content = build_split_export_markdown(
        groups,
        paper_subject=paper_subject.strip(),
        paper_title=paper_title.strip(),
    )

    # 转换为 DOCX
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        docx_path = f.name

    md_path = tempfile.mktemp(suffix=".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    try:
        import subprocess

        subprocess.run(
            [
                "pandoc",
                "-f",
                "markdown+hard_line_breaks",
                "-t",
                "docx",
                "-o",
                docx_path,
                md_path,
            ],
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

    result = await service.wait_for_batch_completion(mineru_task_id, max_wait=600.0)

    if result.status != "done":
        raise HTTPException(
            status_code=400, detail=f"MinerU 解析未完成，当前状态: {result.status}"
        )

    markdown = result.markdown_content

    # 如果 markdown_content 为 None，则从 zip 包下载
    if not markdown and result.full_zip_url:
        try:
            markdown = await service.download_and_extract_markdown(result.full_zip_url)
            print(f"[get_paper_questions] 从 zip 下载了 markdown，长度: {len(markdown) if markdown else 0}")
        except Exception as e:
            print(f"[get_paper_questions] 下载 markdown 失败: {e}")

    # 从 zip 包中提取图片并转换为 base64（与 solve_paper 保持一致）
    if result.full_zip_url:
        try:
            images_base64 = await service.download_and_extract_images(result.full_zip_url)
            print(f"[get_paper_questions] 提取了 {len(images_base64)} 张图片为 base64")

            # 将 markdown 中的相对路径图片替换为 base64 data URL
            if images_base64 and markdown:
                for relative_path, data_url in images_base64.items():
                    markdown = markdown.replace(f"({relative_path})", f"({data_url})")
        except Exception as e:
            print(f"[get_paper_questions] 提取图片失败: {e}")
            images_base64 = {}
    else:
        images_base64 = {}

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
