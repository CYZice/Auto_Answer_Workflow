"""
MinerU v4 全流程测试脚本

测试 PDF 文件 → MinerU v4 解析 → Markdown 下载 → 题目解析
"""
import asyncio
import os
import sys

# 添加 backend 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.mineru_v4_service import MineruV4Service
from app.services.markdown_parser import MarkdownParser, render_paper


TEST_PDF_PATH = "/home/yunzechen/下载/2024春期末模拟卷（控院）/电路模拟题.pdf"


async def test_mineru_v4_parse_file():
    """测试 1: 本地 PDF 文件上传解析"""
    print("=" * 60)
    print("测试 1: MinerU v4 文件解析")
    print("=" * 60)

    service = MineruV4Service()

    # 检查文件
    if not os.path.exists(TEST_PDF_PATH):
        print(f"文件不存在: {TEST_PDF_PATH}")
        return None

    file_size = os.path.getsize(TEST_PDF_PATH) / 1024
    print(f"文件: {TEST_PDF_PATH}")
    print(f"文件大小: {file_size:.1f} KB")

    # 读取文件
    with open(TEST_PDF_PATH, "rb") as f:
        file_content = f.read()

    # 由于 v4 API 需要先获取上传 URL，我们使用 URL 模式
    # 这里演示通过上传文件方式
    # 注意：v4 API 支持批量上传，这里演示单文件上传
    print("使用示例 PDF 进行测试...")

    # 使用示例 PDF 测试 v4 API
    url = "https://cdn-mineru.openxlab.org.cn/demo/example.pdf"
    print(f"提交任务: {url}")

    result = await service.parse_url_and_wait(url, poll_interval=5.0, max_wait=120.0)

    print(f"\n解析结果状态: {result.status}")
    if result.error_msg:
        print(f"错误信息: {result.error_msg}")

    if result.status == "done" and result.markdown_content:
        print(f"Markdown 长度: {len(result.markdown_content)} 字符")
        print(f"\n--- Markdown 内容预览 (前 1500 字符) ---")
        print(result.markdown_content[:1500])
        print("..." if len(result.markdown_content) > 1500 else "")
        print("--- Markdown 预览结束 ---\n")

    return result


async def test_mineru_v4_with_local_file():
    """测试 2: 使用本地文件（需要先上传到可访问 URL）"""
    print("\n" + "=" * 60)
    print("测试 2: 本地 PDF 文件处理")
    print("=" * 60)

    if not os.path.exists(TEST_PDF_PATH):
        print(f"本地文件不存在: {TEST_PDF_PATH}")
        print("提示: 需要将文件上传到可访问的 URL 或使用其他方式")
        return None

    file_size = os.path.getsize(TEST_PDF_PATH) / 1024
    print(f"本地文件大小: {file_size:.1f} KB")

    # v4 API 支持批量上传接口，需要:
    # 1. 调用 /api/v4/file-urls/batch 获取上传 URL
    # 2. PUT 上传文件到 OSS
    # 3. 系统自动检测并提交解析任务
    # 这里暂时跳过，因为需要完整的文件上传流程

    service = MineruV4Service()

    # 尝试提交任务（需要文件可访问）
    # 由于文件在本地，我们先跳过实际提交
    print("本地文件上传需要先获取上传 URL...")
    print("跳过直接提交，等待文件上传接口完善")

    return None


def test_markdown_parser_sample():
    """测试 3: 使用示例 Markdown 测试解析器"""
    print("\n" + "=" * 60)
    print("测试 3: Markdown 解析器 (电路试卷样本)")
    print("=" * 60)

    # 示例电路试卷 Markdown
    sample_markdown = """# 2024春期末模拟卷（控院）

## 一、选择题

1. 在如图所示的电路中，电流 I 为多少？

![题1](https://example.com/circuit1.png)

A. 1A B. 2A C. 3A D. 4A

2. 下列关于电容的说法正确的是（ ）

A. 电容两端电压不能突变
B. 电容电流不能突变
C. 电容越大，容抗越大
D. 电容两端电压与电流同相

## 二、填空题

3. 在 RL 串联电路中，已知 R=10Ω，L=0.1H，则电路的阻抗为 ____ Ω。

4. 理想变压器的条件是：绕组电阻为 ____ ，铁芯损耗为 ____ 。

## 三、计算题

5. 已知某电路如图所示，电源电压 U=12V，R1=2Ω，R2=4Ω，R3=6Ω。
求：（1）等效电阻；（2）各支路电流。

![题5](https://example.com/circuit5.png)

6. 在图示电路中，Us=10V，Is=2A，R=5Ω。求电流 I 和电压 U。

![题6](https://example.com/circuit6.png)
"""

    parser = MarkdownParser()
    questions = parser.parse(sample_markdown)

    print(f"解析出 {len(questions)} 道题目\n")

    for q in questions:
        print(f"题号: {q.number}")
        print(f"题型: {q.question_type}")
        content_preview = q.content[:80] + "..." if len(q.content) > 80 else q.content
        print(f"内容: {content_preview}")
        print(f"图片数: {len(q.images)}")
        print("-" * 40)

    # 测试渲染
    print("\n--- 渲染预览 ---")
    questions_with_images = parser.associate_images(questions, [
        "https://example.com/circuit1.png",
        "https://example.com/circuit5.png",
        "https://example.com/circuit6.png",
    ])
    paper_md = render_paper(questions_with_images, paper_title="2024春期末模拟卷", paper_subject="电路")
    print(paper_md[:1000])
    print("..." if len(paper_md) > 1000 else "")

    return questions


async def main():
    """主测试流程"""
    print("\n" + "#" * 60)
    print("# MinerU v4 全流程测试")
    print("#" * 60 + "\n")

    try:
        # 测试 1: v4 API 测试
        result = await test_mineru_v4_parse_file()

        # 测试 2: 本地文件处理
        await test_mineru_v4_with_local_file()

        # 测试 3: Markdown 解析器
        test_markdown_parser_sample()

        print("\n" + "#" * 60)
        print("# 测试完成!")
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
