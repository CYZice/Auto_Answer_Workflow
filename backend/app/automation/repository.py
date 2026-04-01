from __future__ import annotations

from sqlalchemy.orm import Session

from app.automation.models import AutomationLog, AutomationTask
from app.automation.state_machine import validate_transition


class AutomationRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_task(
        self,
        *,
        task_id: str,
        run_id: str,
        school_name: str,
        topic_title: str,
        topic_image_url: str | None,
        status: str = "discovered",
    ) -> AutomationTask:
        item = (
            self.db.query(AutomationTask)
            .filter(AutomationTask.task_id == task_id)
            .first()
        )
        if item is None:
            item = AutomationTask(
                task_id=task_id,
                run_id=run_id,
                school_name=school_name,
                topic_title=topic_title,
                topic_image_url=topic_image_url,
                status=status,
            )
            self.db.add(item)
        else:
            item.run_id = run_id
            item.school_name = school_name
            item.topic_title = topic_title
            item.topic_image_url = topic_image_url
            item.status = status
        self.db.commit()
        self.db.refresh(item)
        return item

    def get_task(self, task_id: str) -> AutomationTask | None:
        return (
            self.db.query(AutomationTask)
            .filter(AutomationTask.task_id == task_id)
            .first()
        )

    def list_tasks(
        self,
        *,
        run_id: str,
        status: str | None,
        school: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[AutomationTask]]:
        query = self.db.query(AutomationTask).filter(AutomationTask.run_id == run_id)
        if status:
            query = query.filter(AutomationTask.status == status)
        if school:
            query = query.filter(AutomationTask.school_name == school)
        total = query.count()
        items = (
            query.order_by(AutomationTask.updated_at.desc(), AutomationTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items

    def update_status(self, task: AutomationTask, to_status: str) -> AutomationTask:
        validate_transition(task.status, to_status)
        task.status = to_status
        self.db.commit()
        self.db.refresh(task)
        return task

    def update_task_content(
        self,
        task: AutomationTask,
        *,
        final_markdown: str | None = None,
        analysis_markdown: str | None = None,
        extension_text: str | None = None,
        analysis_edited: str | None = None,
        extension_edited: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AutomationTask:
        if final_markdown is not None:
            task.final_markdown = final_markdown
        if analysis_markdown is not None:
            task.analysis_markdown = analysis_markdown
        if extension_text is not None:
            task.extension_text = extension_text
        if analysis_edited is not None:
            task.analysis_edited = analysis_edited
        if extension_edited is not None:
            task.extension_edited = extension_edited
        if error_code is not None:
            task.error_code = error_code
        if error_message is not None:
            task.error_message = error_message
        self.db.commit()
        self.db.refresh(task)
        return task

    def select_tasks(self, run_id: str, task_ids: list[str]) -> int:
        if not task_ids:
            return 0
        items = (
            self.db.query(AutomationTask)
            .filter(
                AutomationTask.run_id == run_id, AutomationTask.task_id.in_(task_ids)
            )
            .all()
        )
        updated = 0
        for item in items:
            if item.status != "discovered":
                continue
            validate_transition(item.status, "selected")
            item.status = "selected"
            item.selected_by_user = 1
            updated += 1
        self.db.commit()
        return updated

    def delete_tasks(self, run_id: str, task_ids: list[str]) -> int:
        if not task_ids:
            return 0
        deleted = (
            self.db.query(AutomationTask)
            .filter(
                AutomationTask.run_id == run_id,
                AutomationTask.task_id.in_(task_ids),
            )
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return int(deleted)

    def list_by_status(self, run_id: str, statuses: list[str]) -> list[AutomationTask]:
        return (
            self.db.query(AutomationTask)
            .filter(
                AutomationTask.run_id == run_id, AutomationTask.status.in_(statuses)
            )
            .order_by(AutomationTask.school_name.asc(), AutomationTask.id.asc())
            .all()
        )

    def append_log(
        self,
        *,
        run_id: str,
        task_id: str | None,
        school_name: str | None,
        step: str,
        level: str,
        message: str,
        payload_summary: str | None = None,
        screenshot_path: str | None = None,
    ) -> AutomationLog:
        row = AutomationLog(
            run_id=run_id,
            task_id=task_id,
            school_name=school_name,
            step=step,
            level=level,
            message=message,
            payload_summary=payload_summary,
            screenshot_path=screenshot_path,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_logs(
        self, run_id: str, task_id: str | None, limit: int
    ) -> list[AutomationLog]:
        query = self.db.query(AutomationLog).filter(AutomationLog.run_id == run_id)
        if task_id:
            query = query.filter(AutomationLog.task_id == task_id)
        return query.order_by(AutomationLog.id.desc()).limit(limit).all()
