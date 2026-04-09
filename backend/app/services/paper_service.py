"""
试卷智能解析服务

处理试卷解析后的自动解题和 DOCX 导出
"""
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.services.markdown_parser import (
    MarkdownParser,
    QuestionWithImage,
    render_paper,
)
from app.services.mineru_service import MineruService


@dataclass
class PaperParseResult:
    """试卷解析结果"""
    markdown: str
    question_count: int
    questions: list[QuestionWithImage]


def parse_paper_markdown(markdown: str) -> PaperParseResult:
    """
    解析试卷 Markdown

    Args:
        markdown: MinerU 返回的 Markdown 内容

    Returns:
        PaperParseResult
    """
    parser = MarkdownParser()
    questions = parser.parse(markdown)

    return PaperParseResult(
        markdown=markdown,
        question_count=len(questions),
        questions=questions,
    )


def generate_paper_with_answers(
    questions: list[QuestionWithImage],
    answers: dict[int, str],  # 题号 -> 答案
    paper_title: str = "",
    paper_subject: str = "",
) -> str:
    """
    生成带答案的试卷 Markdown

    Args:
        questions: 题目列表
        answers: 答案字典 {题号: 答案}
        paper_title: 试卷标题
        paper_subject: 试卷科目

    Returns:
        带答案的 Markdown
    """
    lines = []

    # 标题
    if paper_title:
        lines.append(f"# {paper_title}")
    if paper_subject:
        lines.append(f"**科目：** {paper_subject}")

    lines.append("")
    lines.append("---")
    lines.append("")

    # 按题型分组
    current_type = None
    for qwi in questions:
        q = qwi.question
        if q.question_type != current_type:
            current_type = q.question_type
            lines.append(f"## {current_type}\n")

        # 题目标题
        lines.append(f"**{q.number}.** {q.content}")

        # 题图
        for img in qwi.original_images:
            if img.startswith("data:") or img.startswith("http"):
                lines.append(f"\n![题{q.number}]({img})\n")

        # 答案
        if q.number in answers:
            lines.append(f"\n**答案：** {answers[q.number]}\n")

        lines.append("")

    return "\n".join(lines)


def markdown_to_docx(markdown_content: str, output_path: str) -> str:
    """
    使用 Pandoc 将 Markdown 转换为 DOCX

    Args:
        markdown_content: Markdown 内容
        output_path: 输出文件路径

    Returns:
        生成的 DOCX 文件路径
    """
    # 写入临时 Markdown 文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write(markdown_content)
        md_path = f.name

    try:
        # 使用 pandoc 转换
        result = subprocess.run(
            [
                "pandoc",
                "-f", "markdown+hard_line_breaks",
                "-t", "docx",
                "-o", output_path,
                md_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return output_path
    finally:
        # 清理临时文件
        Path(md_path).unlink(missing_ok=True)


async def export_paper_to_docx(
    mineru_service: MineruService,
    mineru_task_id: str,
    original_images: list[str],
    paper_title: str = "",
    paper_subject: str = "",
) -> tuple[str, str]:
    """
    导出试卷为 DOCX

    Args:
        mineru_service: MinerU 服务
        mineru_task_id: MinerU 任务 ID
        original_images: 原图列表
        paper_title: 试卷标题
        paper_subject: 试卷科目

    Returns:
        (markdown_content, docx_path)
    """
    # 1. 获取解析结果
    result = await mineru_service.get_result(mineru_task_id)
    if result.status != "done":
        raise ValueError(f"MinerU 解析未完成，当前状态: {result.status}")

    markdown = result.markdown_content or await mineru_service.download_markdown(result.markdown_url)

    # 2. 解析 Markdown
    parse_result = parse_paper_markdown(markdown)

    # 3. 关联原图
    parser = MarkdownParser()
    questions_with_images = parser.associate_images(
        [qwi.question for qwi in parse_result.questions],
        original_images,
    )

    # 4. 生成带答案的试卷 Markdown（答案部分暂为空，后续填充）
    paper_md = render_paper(questions_with_images, paper_title, paper_subject)

    # 5. 生成 DOCX
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
        docx_path = f.name

    markdown_to_docx(paper_md, docx_path)

    return markdown, docx_path
