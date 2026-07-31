import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.domain import Task, TaskArtifact


_MATH_EXPRESSION = (
    r"(?:"
    r"\$\$.+?\$\$"
    r"|\$(?!\$)(?:\\.|[^$\r\n])+(?<!\\)\$"
    r"|\\\(.+?\\\)"
    r"|\\\[.+?\\\]"
    r")"
)

_MATH_EXPRESSION_PATTERN = re.compile(
    rf"\A{_MATH_EXPRESSION}\Z",
    re.DOTALL,
)

_CODE_WRAPPED_MATH_PATTERN = re.compile(
    rf"""
    (?P<block>
        ^(?P<indent>[ \t]*)(?P<block_fence>`{{3,}})[^\r\n]*\r?\n
        (?P<block_body>.*?)
        (?:\r?\n)?(?P=indent)(?P=block_fence)[ \t]*(?=\r?$)
    )
    |
    (?P<inline>
        (?<!`)(?P<inline_fence>`{{1,2}})(?!`)
        (?P<leading>[ \t]*)
        (?P<inline_math>{_MATH_EXPRESSION})
        (?P<trailing>[ \t]*)
        (?P=inline_fence)(?!`)
    )
    """,
    re.DOTALL | re.MULTILINE | re.VERBOSE,
)


def sanitize_markdown_math(markdown: str) -> str:
    """移除纯 LaTeX 公式外层误加的 Markdown 代码标记。"""
    if not markdown or "`" not in markdown:
        return markdown

    def unwrap(match: re.Match[str]) -> str:
        block_body = match.group("block_body")
        if block_body is not None:
            math = block_body.strip()
            if _MATH_EXPRESSION_PATTERN.fullmatch(math):
                return f"{match.group('indent')}{math}"
            return match.group(0)

        return (
            f"{match.group('leading')}"
            f"{match.group('inline_math')}"
            f"{match.group('trailing')}"
        )

    return _CODE_WRAPPED_MATH_PATTERN.sub(unwrap, markdown)


def sanitize_stored_markdown_math(db: Session) -> dict[str, int]:
    """清洗数据库中已有的最终答案、预览和 Formatter 产物。"""
    updated_tasks = 0
    updated_artifacts = 0

    tasks = db.query(Task).filter(
        or_(
            Task.final_result.contains("`"),
            Task.question_preview.contains("`"),
            Task.answer_preview.contains("`"),
        )
    )
    for task in tasks.yield_per(200):
        task_changed = False
        for field_name in ("final_result", "question_preview", "answer_preview"):
            current = getattr(task, field_name)
            cleaned = sanitize_markdown_math(current)
            if cleaned != current:
                setattr(task, field_name, cleaned)
                task_changed = True
        if task_changed:
            updated_tasks += 1

    artifacts = db.query(TaskArtifact).filter(
        TaskArtifact.node_name == "formatter",
        TaskArtifact.content.contains("`"),
    )
    for artifact in artifacts.yield_per(200):
        cleaned = sanitize_markdown_math(artifact.content)
        if cleaned != artifact.content:
            artifact.content = cleaned
            updated_artifacts += 1

    return {"tasks": updated_tasks, "artifacts": updated_artifacts}
