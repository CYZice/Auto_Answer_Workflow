"""
Markdown 解析器单元测试

测试 MarkdownParser 的解析能力
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.markdown_parser import MarkdownParser, render_paper


# 测试用 Markdown 试卷内容
SAMPLE_MARKDOWN = """
# 电路模拟题

## 选择题

1. 在如图所示的电路中，电流 I 为多少？

![题1](https://example.com/circuit1.png)

A. 1A B. 2A C. 3A D. 4A

2. 下列关于电容的说法正确的是（ ）

A. 电容两端电压不能突变
B. 电容电流不能突变
C. 电容越大，容抗越大
D. 电容两端电压与电流同相

## 填空题

3. 在 RL 串联电路中，已知 R=10Ω，L=0.1H，则电路的阻抗为 ____ Ω。

4. 理想变压器的条件是：绕组电阻为 ____ ，铁芯损耗为 ____ 。

## 计算题

5. 已知某电路如图所示，电源电压 U=12V，R1=2Ω，R2=4Ω，R3=6Ω。
求：（1）等效电阻；（2）各支路电流。

![题5](https://example.com/circuit2.png)

6. 在图示电路中，Us=10V，Is=2A，R=5Ω。求电流 I 和电压 U。

![题6](https://example.com/circuit3.png)

"""


def test_parse():
    """测试解析功能"""
    print("=" * 60)
    print("测试 1: Markdown 解析")
    print("=" * 60)

    parser = MarkdownParser()
    questions = parser.parse(SAMPLE_MARKDOWN)

    print(f"解析出 {len(questions)} 道题目\n")

    for q in questions:
        print(f"题号: {q.number}")
        print(f"题型: {q.question_type}")
        content_preview = q.content[:80] + "..." if len(q.content) > 80 else q.content
        print(f"内容: {content_preview}")
        print(f"图片数: {len(q.images)}")
        print("-" * 40)

    assert len(questions) == 6, f"期望 6 道题目，实际 {len(questions)}"
    print("\n✓ 解析测试通过\n")
    return questions


def test_split_by_type():
    """测试题型分组"""
    print("=" * 60)
    print("测试 2: 题型分组")
    print("=" * 60)

    parser = MarkdownParser()
    questions = parser.parse(SAMPLE_MARKDOWN)

    grouped = parser.split_by_type(questions)

    for qtype, qs in grouped.items():
        print(f"{qtype}: {len(qs)} 题")
        for q in qs:
            print(f"  - 第 {q.number} 题")

    assert "选择题" in grouped, "缺少选择题"
    assert "填空题" in grouped, "缺少填空题"
    assert "计算题" in grouped, "缺少计算题"
    print("\n✓ 题型分组测试通过\n")
    return grouped


def test_render():
    """测试渲染功能"""
    print("=" * 60)
    print("测试 3: 试卷渲染")
    print("=" * 60)

    parser = MarkdownParser()
    questions = parser.parse(SAMPLE_MARKDOWN)

    # 关联图片
    original_images = [
        "https://example.com/circuit1.png",
        "https://example.com/circuit2.png",
        "https://example.com/circuit3.png",
    ]
    questions_with_images = parser.associate_images(questions, original_images)

    # 渲染试卷
    paper_md = render_paper(questions_with_images, paper_title="2024春期末模拟卷", paper_subject="电路")

    print(f"生成 Markdown 长度: {len(paper_md)} 字符")
    print("\n--- 渲染结果预览 ---")
    print(paper_md[:2000])
    print("..." if len(paper_md) > 2000 else "")
    print("--- 预览结束 ---\n")

    # 验证包含关键内容
    assert "2024春期末模拟卷" in paper_md, "缺少试卷标题"
    assert "电路" in paper_md, "缺少科目"
    assert "选择题" in paper_md, "缺少选择题"
    assert "计算题" in paper_md, "缺少计算题"

    print("✓ 渲染测试通过\n")
    return paper_md


def test_empty_content():
    """测试空内容处理"""
    print("=" * 60)
    print("测试 4: 空内容处理")
    print("=" * 60)

    parser = MarkdownParser()
    questions = parser.parse("")

    print(f"空 Markdown 解析出 {len(questions)} 道题目")
    assert len(questions) == 0, "空内容应返回空列表"
    print("✓ 空内容测试通过\n")


def main():
    """主测试流程"""
    print("\n" + "#" * 60)
    print("# Markdown 解析器单元测试")
    print("#" * 60 + "\n")

    try:
        test_parse()
        test_split_by_type()
        test_render()
        test_empty_content()

        print("#" * 60)
        print("# 全部测试通过!")
        print("#" * 60 + "\n")
        return True

    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}\n")
        return False
    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
