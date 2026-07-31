import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.nodes import formatter as formatter_module
from app.core.database import Base
from app.models.domain import Task, TaskArtifact
from app.services import task_artifacts
from app.services.markdown_sanitizer import (
    sanitize_markdown_math,
    sanitize_stored_markdown_math,
)


def test_sanitize_markdown_math_unwraps_inline_latex():
    source = (
        "边长为 `$l$`，总电阻为 `$R$`，电流依次为 "
        "`$I$`、`$0$`、`$-I$`，方向用 `$\\rightarrow$` 表示。"
    )

    assert sanitize_markdown_math(source) == (
        "边长为 $l$，总电阻为 $R$，电流依次为 "
        "$I$、$0$、$-I$，方向用 $\\rightarrow$ 表示。"
    )


def test_sanitize_markdown_math_unwraps_display_latex():
    source = "推导如下：\n`$$E = Blv$$`\n\n```latex\n\\[I = \\frac{Blv}{R}\\]\n```"

    assert sanitize_markdown_math(source) == (
        "推导如下：\n$$E = Blv$$\n\n\\[I = \\frac{Blv}{R}\\]"
    )


def test_sanitize_markdown_math_preserves_non_math_code():
    source = (
        "命令变量为 `$HOME`，公式源码示例：\n"
        "```markdown\n这里应保留示例 `$x$` 和普通代码 `E = Blv`。\n```"
    )

    assert sanitize_markdown_math(source) == source


def test_formatter_persists_and_returns_sanitized_result(monkeypatch):
    formatted = "设线框边长为 `$l$`，则 `$$E = Blv$$`。"
    persisted = {}

    async def fake_format_solution(*args, **kwargs):
        return {"formatted_result": formatted, "tokens": 7}

    def fake_persist_task_artifact(task_id, node_name, content, metadata, revision):
        persisted.update(
            task_id=task_id,
            node_name=node_name,
            content=content,
            metadata=metadata,
            revision=revision,
        )

    monkeypatch.setattr(formatter_module, "format_node_sync", lambda task_id: False)
    monkeypatch.setattr(formatter_module, "format_solution", fake_format_solution)
    monkeypatch.setattr(task_artifacts, "persist_task_artifact", fake_persist_task_artifact)

    result = asyncio.run(
        formatter_module.format_node(
            {
                "task_id": "task_sanitize",
                "draft_solution": "草稿",
                "agent_configs": {},
                "image_urls": [],
                "question_text": "题目",
                "total_tokens": 2,
                "input_revision": 3,
            }
        )
    )

    expected = "设线框边长为 $l$，则 $$E = Blv$$。"
    assert result["status"] == "completed"
    assert result["final_result"] == expected
    assert result["total_tokens"] == 9
    assert persisted["content"] == expected
    assert persisted["revision"] == 3


def test_sanitize_stored_markdown_math_updates_existing_records():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine)

    with testing_session() as db:
        db.add_all(
            [
                Task(
                    task_id="dirty_task",
                    thread_id="thread_dirty",
                    image_url="",
                    state="completed",
                    final_result="题目含 `$l$`。\n【正解】`$$E = Blv$$`",
                    question_preview="题目含 `$l$`。",
                    answer_preview="【正解】`$$E = Blv$$`",
                ),
                Task(
                    task_id="clean_task",
                    thread_id="thread_clean",
                    image_url="",
                    state="completed",
                    final_result="命令变量为 `$HOME`。",
                ),
                TaskArtifact(
                    task_id="dirty_task",
                    node_name="formatter",
                    input_revision=1,
                    content="结果为 `$I$`。",
                ),
                TaskArtifact(
                    task_id="dirty_task",
                    node_name="solver",
                    input_revision=1,
                    content="草稿示例 `$I$`。",
                ),
            ]
        )
        db.commit()

        counts = sanitize_stored_markdown_math(db)
        db.commit()

        dirty_task = db.get(Task, "dirty_task")
        clean_task = db.get(Task, "clean_task")
        formatter_artifact = (
            db.query(TaskArtifact)
            .filter(TaskArtifact.node_name == "formatter")
            .one()
        )
        solver_artifact = (
            db.query(TaskArtifact)
            .filter(TaskArtifact.node_name == "solver")
            .one()
        )

        assert counts == {"tasks": 1, "artifacts": 1}
        assert dirty_task.final_result == "题目含 $l$。\n【正解】$$E = Blv$$"
        assert dirty_task.question_preview == "题目含 $l$。"
        assert dirty_task.answer_preview == "【正解】$$E = Blv$$"
        assert clean_task.final_result == "命令变量为 `$HOME`。"
        assert formatter_artifact.content == "结果为 $I$。"
        assert solver_artifact.content == "草稿示例 `$I$`。"

        assert sanitize_stored_markdown_math(db) == {"tasks": 0, "artifacts": 0}
