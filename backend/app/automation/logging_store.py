from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.automation.repository import AutomationRepository


class AutomationLoggingStore:
    def __init__(self, logs_dir: str = "./automation_logs"):
        self.logs_dir = logs_dir
        os.makedirs(self.logs_dir, exist_ok=True)

    def append(
        self,
        db: Session,
        *,
        run_id: str,
        task_id: str | None,
        school_name: str | None,
        step: str,
        level: str,
        message: str,
        payload_summary: str | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        repo = AutomationRepository(db)
        repo.append_log(
            run_id=run_id,
            task_id=task_id,
            school_name=school_name,
            step=step,
            level=level,
            message=message,
            payload_summary=payload_summary,
            screenshot_path=screenshot_path,
        )

        row = {
            "run_id": run_id,
            "task_id": task_id,
            "school_name": school_name,
            "step": step,
            "level": level,
            "message": message,
            "payload_summary": payload_summary,
            "screenshot_path": screenshot_path,
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        file_path = os.path.join(self.logs_dir, f"{run_id}.jsonl")
        with open(file_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
