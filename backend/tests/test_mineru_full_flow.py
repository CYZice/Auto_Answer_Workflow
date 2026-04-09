"""
MinerU 全流程测试脚本

测试 PDF 文件上传 → MinerU 解析 → Markdown 下载 → 题目解析
"""
import asyncio
import sys
import os

# 添加 backend 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.mineru_service import get_mineru_service, MineruService
from app.services.markdown_parser import MarkdownParser, render_paper


TEST_PDF_PATH = "/home/yunzechen/下载/2024春期末模拟卷（控院）/电路模拟题.pdf"


async def test_mineru_parse_file():
    """测试 1: 上传 PDF 文件到 MinerU 并等待解析完成"""
    print("=" * 60)
    print("测试 1: MinerU 文件解析")
    print("=" * 60)

    mineru_service = get_mineru_service()

    # 读取 PDF 文件
    with open(TEST_PDF_PATH, "rb") as f:
        file_content = f.read()

    print(f"文件大小: {len(file_content) / 1024:.1f} KB")
    print("正在上传到 MinerU...")

    # 上传文件获取 task_id
    mineru_task_id = await mineru_service.parse_file(
        file_content=file_content,
        filename="电路模拟题.pdf",
        language="ch",
        enable_table=True,
        is_ocr=False,
        enable_formula=True,
    )

    print(f"任务 ID: {mineru_task_id}")
    print("等待解析完成...")

    # 等待解析完成
    result = await mineru_service.wait_for_completion(
        mineru_task_id,
        poll_interval=3.0,
        max_wait=300.0,
    )

    print(f"\n解析结果状态: {result.status}")
    if result.error_msg:
        print(f"错误信息: {result.error_msg}")

    if result.status == "done":
        print(f"Markdown URL: {result.markdown_url}")
        print(f"\n--- Markdown 内容预览 (前 2000 字符) ---")
        print(result.markdown_content[:2000] if result.markdown_content else "无内容")
        print("..." if len(result.markdown_content or "") > 2000 else "")
        print("--- Markdown 预览结束 ---\n")

    return result


def test_markdown_parser(markdown_content: str):
    """测试 2: Markdown 解析器"""
    print("=" * 60)
    print("测试 2: Markdown 解析器")
    print("=" * 60)

    if not markdown_content:
        print("Markdown 内容为空，跳过解析测试")
        return None

    parser = MarkdownParser()
    questions = parser.parse(markdown_content)

    print(f"解析出 {len(questions)} 道题目")
    print()

    for q in questions[:10]:  # 只显示前 10 题
        print(f"题号: {q.number}")
        print(f"题型: {q.question_type}")
        print(f"内容: {q.content[:100]}..." if len(q.content) > 100 else f"内容: {q.content}")
        print(f"图片数: {len(q.images)}")
        print("-" * 40)

    if len(questions) > 10:
        print(f"... 还有 {len(questions) - 10} 道题目")

    return questions


def test_render_paper(questions):
    """测试 3: 渲染试卷 Markdown"""
    print("\n" + "=" * 60)
    print("测试 3: 渲染试卷 Markdown")
    print("=" * 60)

    if not questions:
        print("题目列表为空，跳过渲染测试")
        return

    paper_md = render_paper(questions, paper_title="2024春期末模拟卷", paper_subject="电路")

    print(f"生成 Markdown 长度: {len(paper_md)} 字符")
    print("\n--- 渲染结果预览 (前 1500 字符) ---")
    print(paper_md[:1500])
    print("..." if len(paper_md) > 1500 else "")
    print("--- 渲染预览结束 ---\n")

    return paper_md


async def main():
    """主测试流程"""
    print("\n" + "#" * 60)
    print("# MinerU 全流程测试")
    print("#" * 60 + "\n")

    try:
        # 测试 1: MinerU 解析
        result = await test_mineru_parse_file()

        if result.status != "done":
            print(f"\n测试失败: MinerU 解析状态为 {result.status}")
            return False

        # 测试 2: Markdown 解析
        questions = test_markdown_parser(result.markdown_content)

        # 测试 3: 渲染试卷
        if questions:
            test_render_paper(questions)

        print("\n" + "#" * 60)
        print("# 全部测试完成!")
        print("#" * 60 + "\n")

        return True

    except Exception as e:
        print(f"\n测试过程发生异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
