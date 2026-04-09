"""
Markdown 试卷解析器

解析 MinerU 返回的 Markdown 格式试卷，提取题目结构
"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Question:
    """单个题目"""
    number: int  # 题号
    question_type: str  # 题型（选择题、填空题、解答题等）
    content: str  # 题干文本
    images: list[str] = field(default_factory=list)  # 题图 URL 列表
    answer: Optional[str] = None  # 答案（如果有）
    sub_questions: list["Question"] = field(default_factory=list)  # 子题（如 1.1, 1.2）


@dataclass
class QuestionWithImage:
    """带题图的题目"""
    question: Question
    original_images: list[str]  # 关联的原题图片


# 题型识别正则：大写数字 + 顿号/句号 + 空格，后面的文字即为题型名称
# 支持多种格式：一、选择题，一 、 直流电路 等
CHINESE_NUMERAL_PATTERN = r"^[一二三四五六七八九十]+\s*[、.]\s*"

# 用于提取题型名称：从大写数字+顿号位置到行尾的内容
def extract_question_type_name(line: str) -> str:
    """从标题行提取题型名称"""
    match = re.search(CHINESE_NUMERAL_PATTERN, line)
    if match:
        # 去掉匹配到的部分，剩余就是题型名称
        name = line[match.end():].strip()
        # 去掉可能的括号内容（如分数信息）
        name = re.sub(r"[（(].*$", "", name).strip()
        return name if name else "未分类"
    return "未分类"

# 题号识别正则
NUMBER_PATTERNS = [
    (r"^(\d+)[.、]\s*(.+)", "decimal"),  # 1. 2. 或 1、 2、
    (r"^[（(](\d+)[)）]\s*(.+)", "paren"),  # (1) (2)
    (r"^\[(\d+)\]\s*(.+)", "bracket"),  # [1] [2]
    (r"^第(\d+)题\s*(.+)", "chinese"),  # 第1题
    (r"^(\d+)\s+(.+)", "plain_num"),  # 2 用结点电压法（数字+空格+内容）
    (r"^(\d+)\s*$", "plain"),  # 仅数字行
]

# 图片正则
IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^\)]+)\)")


def extract_images(text: str) -> tuple[str, list[str]]:
    """从文本中提取所有图片 URL，返回 (无图片文本, 图片列表)"""
    images = IMAGE_PATTERN.findall(text)
    image_urls = [img[1] for img in images]
    # 移除图片 markdown 语法
    clean_text = IMAGE_PATTERN.sub(r"[图片: \1]", text)
    return clean_text, image_urls


def parse_question_number(line: str) -> tuple[Optional[int], Optional[str], str]:
    """
    解析题目行，返回 (题号, 匹配类型, 剩余内容)

    例如:
    "1. 这是一道选择题" -> (1, "decimal", "这是一道选择题")
    "(2) 填空题内容" -> (2, "paren", "填空题内容")
    """
    for pattern, match_type in NUMBER_PATTERNS:
        match = re.match(pattern, line.strip())
        if match:
            groups = match.groups()
            if match_type == "decimal":
                return int(groups[0]), match_type, groups[1]
            elif match_type == "paren":
                return int(groups[0]), match_type, groups[1]
            elif match_type == "bracket":
                return int(groups[0]), match_type, groups[1]
            elif match_type == "chinese":
                return int(groups[0]), match_type, groups[1]
            elif match_type == "plain_num":
                return int(groups[0]), match_type, groups[1]
            elif match_type == "plain":
                return int(groups[0]), match_type, ""
    return None, None, line


def detect_question_type(line: str, current_type: str) -> tuple[str, bool]:
    """
    检测题型标题行

    Returns:
        (新题型名称, 是否检测到新题型)
    """
    line_stripped = line.strip()
    # 去掉 # 标题标记
    if line_stripped.startswith("#"):
        line_after_hash = line_stripped.lstrip("#").strip()
    else:
        line_after_hash = line_stripped

    # 检查是否匹配大写数字+顿号模式（如 一、选择题 或 一、直流电路）
    if re.match(CHINESE_NUMERAL_PATTERN, line_after_hash):
        new_type = extract_question_type_name(line_after_hash)
        return new_type, True

    # 检查是否为 # 开头的纯题型名称（## 选择题、## 填空题 等）
    if line_stripped.startswith("#") and not re.match(CHINESE_NUMERAL_PATTERN, line_after_hash):
        # 去掉括号中的内容
        type_name = re.sub(r"[（(].*$", "", line_after_hash).strip()
        if type_name:
            return type_name, True

    return current_type, False


class MarkdownParser:
    """Markdown 试卷解析器"""

    def parse(self, markdown: str) -> list[Question]:
        """
        解析 Markdown 试卷，返回题目列表

        Args:
            markdown: MinerU 返回的 Markdown 内容

        Returns:
            题目列表
        """
        lines = markdown.split("\n")
        questions: list[Question] = []
        current_type = "未分类"
        current_content_lines: list[str] = []
        current_images: list[str] = []
        current_number: Optional[int] = None

        def flush_question():
            """将当前累积的内容 flush 为一个题目"""
            nonlocal questions, current_content_lines, current_images, current_number
            if current_content_lines or current_number is not None:
                content = "\n".join(current_content_lines).strip()
                if content or current_number is not None:
                    questions.append(Question(
                        number=current_number or len(questions) + 1,
                        question_type=current_type,
                        content=content,
                        images=current_images.copy(),
                    ))
                current_content_lines = []
                current_images = []
                current_number = None

        for line in lines:
            stripped = line.strip()

            # 跳过空行
            if not stripped:
                continue

            # 检测题型标题（# 开头的行）
            if stripped.startswith("#"):
                flush_question()
                new_type, is_new_type = detect_question_type(stripped, current_type)
                if is_new_type:
                    current_type = new_type
                continue

            # 检测大写数字+顿号开头的行（无 # 号的章节标题）
            new_type, is_new_type = detect_question_type(stripped, current_type)
            if is_new_type:
                flush_question()
                current_type = new_type
                continue

            # 检测题号行
            number, match_type, rest = parse_question_number(stripped)

            if number is not None and match_type is not None:
                # 如果有之前累积的内容，先 flush
                if current_content_lines or current_number is not None:
                    flush_question()

                current_number = number
                # 提取图片并清理内容中的图片 markdown
                content_with_images = rest or ""
                clean_content, imgs = extract_images(content_with_images)
                if clean_content:
                    current_content_lines.append(clean_content)
                current_images.extend(imgs)
            else:
                # 普通内容行，提取图片并清理 markdown
                clean_line, imgs = extract_images(stripped)
                if clean_line:
                    current_content_lines.append(clean_line)
                current_images.extend(imgs)

        # Flush 最后一个题目
        flush_question()

        return questions

    def split_by_type(self, questions: list[Question]) -> dict[str, list[Question]]:
        """
        按题型分组

        Args:
            questions: 题目列表

        Returns:
            按题型分组的字典
        """
        grouped: dict[str, list[Question]] = {}
        for q in questions:
            if q.question_type not in grouped:
                grouped[q.question_type] = []
            grouped[q.question_type].append(q)
        return grouped

    def associate_images(
        self,
        questions: list[Question],
        original_images: list[str],
    ) -> list[QuestionWithImage]:
        """
        将原图与题目关联

        策略：按位置顺序一一对应

        Args:
            questions: 题目列表
            original_images: 原图 URL 列表

        Returns:
            带题图的题目列表
        """
        result: list[QuestionWithImage] = []
        for i, question in enumerate(questions):
            # 找出所有与当前题目关联的图片
            # 策略：如果题目本身没有图片，则尝试从 original_images 中按顺序获取
            if question.images:
                # 题目内容已包含图片，使用题目自带的图片
                assoc_images = question.images
            elif i < len(original_images):
                # 题目没有图片，但有原图，按位置分配
                assoc_images = [original_images[i]]
            else:
                assoc_images = []

            result.append(QuestionWithImage(
                question=question,
                original_images=assoc_images,
            ))

        return result


def render_question_with_images(qwi: QuestionWithImage) -> str:
    """
    将带题图的题目渲染为 Markdown

    Args:
        qwi: 带题图的题目

    Returns:
        Markdown 格式字符串
    """
    lines = []
    q = qwi.question

    # 题目标题
    lines.append(f"**{q.number}.** {q.content}")

    # 插入题图
    for img in qwi.original_images:
        if img.startswith("data:") or img.startswith("http"):
            lines.append(f"\n![题{q.number}]({img})\n")

    return "\n".join(lines)


def render_paper(
    questions: list[QuestionWithImage],
    paper_title: str = "",
    paper_subject: str = "",
) -> str:
    """
    渲染整张试卷为 Markdown

    Args:
        questions: 带题图的题目列表
        paper_title: 试卷标题
        paper_subject: 试卷科目

    Returns:
        完整试卷 Markdown
    """
    lines = []

    # 标题
    if paper_title:
        lines.append(f"# {paper_title}")
    if paper_subject:
        lines.append(f"**科目：** {paper_subject}")

    lines.append("")

    # 按题型分组渲染
    current_type = None
    for qwi in questions:
        q = qwi.question
        if q.question_type != current_type:
            current_type = q.question_type
            lines.append(f"\n## {current_type}\n")
        lines.append(render_question_with_images(qwi))
        lines.append("")

    return "\n".join(lines)
