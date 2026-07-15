import json
from typing import Any

from app.core.database import SessionLocal
from app.models.domain import Task, TaskArtifact


def persist_task_artifact(
    task_id: str,
    node_name: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    input_revision: int | None = None,
) -> None:
    if not task_id or not content:
        return
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == task_id).first()
        revision = input_revision or (task.input_revision if task and task.input_revision else 1)
        db.add(
            TaskArtifact(
                task_id=task_id,
                node_name=node_name,
                input_revision=revision,
                content=content,
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            )
        )
        if task:
            task.current_node = node_name
        db.commit()


def latest_task_artifact(
    task_id: str,
    node_name: str,
    input_revision: int | None = None,
) -> TaskArtifact | None:
    with SessionLocal() as db:
        query = db.query(TaskArtifact).filter(
            TaskArtifact.task_id == task_id,
            TaskArtifact.node_name == node_name,
        )
        if input_revision is not None:
            query = query.filter(TaskArtifact.input_revision == input_revision)
        artifact = query.order_by(TaskArtifact.id.desc()).first()
        if not artifact:
            return None
        db.expunge(artifact)
        return artifact
