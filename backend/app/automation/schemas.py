from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TaskStatusLiteral = Literal[
    "discovered",
    "selected",
    "grabbed",
    "solving",
    "solve_failed",
    "filled",
    "review_pending",
    "ready_to_submit",
    "submitting",
    "submitted",
    "failed_submit",
    "skipped",
    "paused",
    "stopped",
]

RunStateLiteral = Literal["idle", "running", "paused", "stopped"]
RunModeLiteral = Literal["headed", "headless"]


class AckResp(BaseModel):
    ok: bool = True
    message: str = "accepted"


class StartSessionReq(BaseModel):
    username: str
    password: str
    mode: RunModeLiteral = "headed"


class StartSessionResp(BaseModel):
    run_id: str
    mode: RunModeLiteral
    state: RunStateLiteral


class RunReq(BaseModel):
    run_id: str


class ScanReq(BaseModel):
    run_id: str
    school_id: int | None = None  # None 表示扫描所有学校


class BatchReq(BaseModel):
    run_id: str
    limit: int = Field(default=0, ge=0)


class SelectTasksReq(BaseModel):
    run_id: str
    task_ids: list[str] = Field(default_factory=list)


class DeleteTasksReq(BaseModel):
    run_id: str
    task_ids: list[str] = Field(default_factory=list)


class SaveReviewReq(BaseModel):
    analysis_text: str = ""
    extension_text: str = ""


class TaskItem(BaseModel):
    task_id: str
    run_id: str
    school_name: str
    topic_title: str
    topic_image_url: str | None = None
    status: TaskStatusLiteral
    final_markdown: str | None = None
    analysis_markdown: str | None = None
    extension_text: str | None = None
    analysis_edited: str | None = None
    extension_edited: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class TaskResp(BaseModel):
    item: TaskItem


class TaskListResp(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[TaskItem]


class LogItem(BaseModel):
    id: int
    run_id: str
    task_id: str | None = None
    school_name: str | None = None
    step: str
    level: str
    message: str
    payload_summary: str | None = None
    screenshot_path: str | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class LogListResp(BaseModel):
    items: list[LogItem] = Field(default_factory=list)


class RunStatusResp(BaseModel):
    run_id: str
    mode: RunModeLiteral
    state: RunStateLiteral
    current_task_id: str | None = None
