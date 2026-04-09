"""
MinerU API 路由

提供试卷智能解析的 API 接口（使用 v4 精准解析 API）
"""
import asyncio
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.mineru_v4_service import get_mineru_v4_service, MineruV4Service, MineruV4ParseResult

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
    status: str  # pending, running, done, failed
    markdown_url: Optional[str] = None
    markdown_content: Optional[str] = None
    error_message: Optional[str] = None
    extract_progress: Optional[dict] = None


class MineruUploadUrlResponse(BaseModel):
    """获取上传 URL 响应"""
    batch_id: str
    upload_url: str


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


# === 路由实现 ===

@router.post("/parse/file", response_model=MineruUploadUrlResponse)
async def get_upload_url(
    file: UploadFile = File(..., description="试卷图片或 PDF 文件"),
):
    """
    获取文件上传 URL

    返回上传 URL，客户端上传文件后系统自动提交解析任务
    """
    service = get_v4_service()

    # 获取上传 URL
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

    return MineruUploadUrlResponse(
        batch_id=batch_id,
        upload_url=upload_url,
    )


@router.post("/parse/{batch_id}/upload", response_model=MineruParseResponse)
async def upload_file(
    batch_id: str,
    file: UploadFile = File(..., description="试卷图片或 PDF 文件"),
):
    """
    上传文件并提交解析任务

    文件上传到 OSS 后，系统自动检测并提交解析任务
    """
    service = get_v4_service()

    # 读取文件内容
    file_content = await file.read()

    # 1. 获取上传 URL
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

    # 2. 上传文件到 OSS
    upload_resp = requests.put(upload_url, data=file_content, timeout=60)
    if upload_resp.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail=f"文件上传失败, HTTP {upload_resp.status_code}")

    # 3. 轮询等待解析完成
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
    """
    URL 解析

    提交远程文件 URL，由 MinerU 下载并解析
    """
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
    """
    查询解析结果

    支持 v4 API 的 task_id 和 batch_id
    """
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
    poll_interval: Optional[float] = Query(default=3.0, description="轮询间隔（秒）"),
    max_wait: Optional[float] = Query(default=600.0, description="最大等待时间（秒）"),
):
    """
    等待解析完成

    阻塞等待 MinerU 解析任务完成，并返回 Markdown 内容
    """
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


# === 试卷解析与导出 ===

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


@router.post("/paper/{task_id}/solve", response_model=PaperSolveResponse)
async def solve_paper(
    task_id: str,
    request: PaperSolveRequest,
):
    """
    解析试卷并自动开始解题
    """
    service = get_v4_service()

    try:
        # 获取解析结果
        result = await service.wait_for_completion(task_id, max_wait=600.0)

        if result.status != "done":
            raise HTTPException(status_code=400, detail=f"MinerU 解析未完成，当前状态: {result.status}")

        # 下载并解析 markdown
        markdown = result.markdown_content
        if not markdown and result.full_zip_url:
            markdown = await download_and_extract_markdown(result.full_zip_url)

        if not markdown:
            raise HTTPException(status_code=500, detail="无法获取 Markdown 内容")

        # 解析题目
        from app.services.markdown_parser import MarkdownParser
        parser = MarkdownParser()
        questions = parser.parse(markdown)

        paper_task_id = f"paper_{task_id}"

        return PaperSolveResponse(
            paper_task_id=paper_task_id,
            question_count=len(questions),
            status="initiated",
            message=f"试卷解析完成，共 {len(questions)} 道题目，解题流程已启动",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/paper/{task_id}/export/docx")
async def export_paper_docx(
    task_id: str,
    original_images: Optional[str] = Query(default=None, description="原图 Base64 列表（JSON）"),
    paper_title: Optional[str] = Query(default="", description="试卷标题"),
    paper_subject: Optional[str] = Query(default="", description="试卷科目"),
):
    """
    导出试卷为 DOCX
    """
    import json
    import subprocess
    from pathlib import Path

    service = get_v4_service()

    try:
        images = []
        if original_images:
            try:
                images = json.loads(original_images)
            except json.JSONDecodeError:
                pass

        # 获取解析结果
        result = await service.wait_for_completion(task_id, max_wait=600.0)

        if result.status != "done":
            raise HTTPException(status_code=400, detail=f"MinerU 解析未完成，当前状态: {result.status}")

        # 下载并解析 markdown
        markdown = result.markdown_content
        if not markdown and result.full_zip_url:
            markdown = await download_and_extract_markdown(result.full_zip_url)

        if not markdown:
            raise HTTPException(status_code=500, detail="无法获取 Markdown 内容")

        # 解析 Markdown
        from app.services.markdown_parser import MarkdownParser, render_paper
        parser = MarkdownParser()
        questions = parser.parse(markdown)
        questions_with_images = parser.associate_images(questions, images)
        paper_md = render_paper(questions_with_images, paper_title, paper_subject)

        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
            docx_path = f.name

        md_path = tempfile.mktemp(suffix='.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(paper_md)

        try:
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/paper/{task_id}/questions")
async def get_paper_questions(
    task_id: str,
):
    """
    获取试卷题目列表

    返回解析后的题目结构
    """
    service = get_v4_service()

    try:
        # 获取解析结果
        result = await service.wait_for_completion(task_id, max_wait=600.0)

        if result.status != "done":
            raise HTTPException(status_code=400, detail=f"MinerU 解析未完成，当前状态: {result.status}")

        # 下载并解析 markdown
        markdown = result.markdown_content
        if not markdown and result.full_zip_url:
            markdown = await download_and_extract_markdown(result.full_zip_url)

        if not markdown:
            raise HTTPException(status_code=500, detail="无法获取 Markdown 内容")

        # 解析 Markdown
        from app.services.markdown_parser import MarkdownParser
        parser = MarkdownParser()
        questions = parser.parse(markdown)

        # 按题型分组
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
                qtype: [
                    {"number": q.number, "content": q.content[:100]}
                    for q in qs
                ]
                for qtype, qs in grouped.items()
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
