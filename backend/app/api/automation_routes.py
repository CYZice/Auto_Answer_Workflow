from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.automation.schemas import (
    AckResp,
    BatchReq,
    LogListResp,
    RunReq,
    RunStatusResp,
    SaveReviewReq,
    SelectTasksReq,
    StartSessionReq,
    StartSessionResp,
    TaskListResp,
    TaskResp,
)
from app.automation.service import automation_service

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.post("/session/start", response_model=StartSessionResp)
async def start_session(req: StartSessionReq):
    run = await automation_service.start_session(req)
    return StartSessionResp(run_id=run.run_id, mode=run.mode, state=run.state)


@router.get("/run/status", response_model=RunStatusResp)
async def get_run_status(run_id: str):
    try:
        run = automation_service.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RunStatusResp(
        run_id=run.run_id,
        mode=run.mode,
        state=run.state,
        current_task_id=run.current_task_id,
    )


@router.post("/scan/start", response_model=AckResp)
async def start_scan(req: RunReq):
    try:
        await automation_service.trigger_scan(req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AckResp(message="scan started")


@router.get("/tasks", response_model=TaskListResp)
def list_tasks(
    run_id: str,
    status: str | None = None,
    school: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
):
    total, items = automation_service.list_tasks(
        run_id=run_id,
        status=status,
        school=school,
        page=page,
        page_size=page_size,
    )
    return TaskListResp(total=total, page=page, page_size=page_size, items=items)


@router.post("/tasks/select", response_model=AckResp)
async def select_tasks(req: SelectTasksReq):
    count = await automation_service.select_tasks(req.run_id, req.task_ids)
    return AckResp(message=f"selected: {count}")


@router.post("/grab/start", response_model=AckResp)
async def start_grab(req: BatchReq):
    try:
        await automation_service.trigger_grab(req.run_id, req.limit)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AckResp(message="grab started")


@router.post("/solve/start", response_model=AckResp)
async def start_solve(req: BatchReq):
    try:
        await automation_service.trigger_solve(req.run_id, req.limit)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AckResp(message="solve started")


@router.post("/task/{task_id}/review/save", response_model=TaskResp)
async def save_review(task_id: str, req: SaveReviewReq):
    try:
        item = await automation_service.save_review(
            task_id, req.analysis_text, req.extension_text
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskResp(item=item)


@router.post("/task/{task_id}/confirm-submit", response_model=TaskResp)
async def confirm_submit(task_id: str):
    try:
        item = await automation_service.confirm_submit(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskResp(item=item)


@router.post("/run/pause", response_model=AckResp)
async def pause_run(req: RunReq):
    await automation_service.pause(req.run_id)
    return AckResp(message="paused")


@router.post("/run/resume", response_model=AckResp)
async def resume_run(req: RunReq):
    await automation_service.resume(req.run_id)
    return AckResp(message="running")


@router.post("/run/stop", response_model=AckResp)
async def stop_run(req: RunReq):
    await automation_service.stop(req.run_id)
    return AckResp(message="stopped")


@router.get("/logs", response_model=LogListResp)
def list_logs(
    run_id: str,
    task_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    return LogListResp(
        items=automation_service.list_logs(run_id=run_id, task_id=task_id, limit=limit)
    )
