from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.automation.browser_worker import BrowserWorker
from app.automation.logging_store import AutomationLoggingStore
from app.automation.markdown_renderer import MarkdownRenderer
from app.automation.repository import AutomationRepository
from app.automation.schemas import StartSessionReq
from app.automation.state_machine import validate_transition
from app.core.database import SessionLocal


@dataclass
class RunRuntime:
    run_id: str
    mode: str
    state: str = "idle"
    current_task_id: str | None = None
    stop_event: asyncio.Event | None = None
    current_job: asyncio.Task | None = None


class AutomationService:
    def __init__(self):
        self._runs: dict[str, RunRuntime] = {}
        self._lock = asyncio.Lock()
        self._browser = BrowserWorker()
        self._renderer = MarkdownRenderer()
        self._logs = AutomationLoggingStore()
        self._review_deadlines: dict[str, float] = {}
        self._review_timeout_seconds = 600
        self._run_lock = asyncio.Lock()
        self._submit_lock = asyncio.Lock()
        self._workflow_api_base = os.getenv(
            "AUTOMATION_WORKFLOW_API_BASE", "http://127.0.0.1:8080"
        ).rstrip("/")

    async def start_session(self, req: StartSessionReq) -> RunRuntime:
        run = RunRuntime(
            run_id=f"run_{uuid.uuid4().hex[:10]}",
            mode=req.mode,
            state="idle",
            stop_event=asyncio.Event(),
        )
        async with self._lock:
            self._runs[run.run_id] = run
        # 账号密码仅会话内存持有，不落盘
        await self._browser.start_session(
            run_id=run.run_id,
            username=req.username,
            password=req.password,
            mode=req.mode,
        )
        return run

    def get_run(self, run_id: str) -> RunRuntime:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        return run

    def _check_stopped(self, run: RunRuntime) -> None:
        if run.stop_event and run.stop_event.is_set():
            raise asyncio.CancelledError("run stopped")

    def _log(
        self,
        db: Session,
        run_id: str,
        step: str,
        message: str,
        *,
        task_id: str | None = None,
        school_name: str | None = None,
        level: str = "INFO",
    ) -> None:
        self._logs.append(
            db,
            run_id=run_id,
            task_id=task_id,
            school_name=school_name,
            step=step,
            level=level,
            message=message,
        )

    def _log_runtime(
        self,
        run_id: str,
        step: str,
        message: str,
        *,
        level: str = "INFO",
        task_id: str | None = None,
        school_name: str | None = None,
    ) -> None:
        with SessionLocal() as db:
            self._log(
                db,
                run_id,
                step,
                message,
                task_id=task_id,
                school_name=school_name,
                level=level,
            )

    async def start_scan(self, run_id: str) -> None:
        async with self._run_lock:
            run = self.get_run(run_id)
            run.state = "running"
            run.stop_event = run.stop_event or asyncio.Event()
            self._log_runtime(run_id, "scan", "scan job started")

            def _scan_logger(level: str, step: str, message: str) -> None:
                self._log_runtime(run_id, step, message, level=level)

            rows = await self._browser.scan_discovered_tasks(
                run_id, on_log=_scan_logger
            )
            with SessionLocal() as db:
                repo = AutomationRepository(db)
                for row in rows:
                    self._check_stopped(run)
                    repo.upsert_task(
                        task_id=row["task_id"],
                        run_id=run_id,
                        school_name=row["school_name"],
                        topic_title=row["topic_title"],
                        topic_image_url=row.get("topic_image_url"),
                        status="discovered",
                    )
                self._log(db, run_id, "scan", f"discovered tasks: {len(rows)}")
            self._log_runtime(run_id, "scan", "scan job finished")
            run.state = "idle"

    async def _run_job(self, run_id: str, name: str, coro):
        run = self.get_run(run_id)
        run.current_job = asyncio.current_task()
        try:
            await coro
        except asyncio.CancelledError:
            with SessionLocal() as db:
                self._log(db, run_id, name, f"{name} cancelled by stop", level="WARN")
            raise
        except Exception as exc:
            run.state = "idle"
            with SessionLocal() as db:
                self._log(db, run_id, name, f"{name} failed: {exc}", level="ERROR")
        finally:
            if run.current_job is asyncio.current_task():
                run.current_job = None

    def _ensure_idle(self, run: RunRuntime) -> None:
        if run.current_job and not run.current_job.done():
            raise ValueError("run already has an active job")

    async def trigger_scan(self, run_id: str) -> None:
        run = self.get_run(run_id)
        self._ensure_idle(run)
        run.stop_event = asyncio.Event()
        run.current_job = asyncio.create_task(
            self._run_job(run_id, "scan", self.start_scan(run_id))
        )

    async def trigger_grab(self, run_id: str, limit: int = 0) -> None:
        run = self.get_run(run_id)
        self._ensure_idle(run)
        run.stop_event = asyncio.Event()
        run.current_job = asyncio.create_task(
            self._run_job(run_id, "grab", self.start_grab(run_id, limit))
        )

    async def trigger_solve(self, run_id: str, limit: int = 0) -> None:
        run = self.get_run(run_id)
        self._ensure_idle(run)
        run.stop_event = asyncio.Event()
        run.current_job = asyncio.create_task(
            self._run_job(run_id, "solve", self.start_solve(run_id, limit))
        )

    def _expire_review_timeouts(self, run_id: str) -> None:
        now = time.monotonic()
        expired_task_ids = [
            task_id
            for task_id, deadline in self._review_deadlines.items()
            if deadline <= now
        ]
        if not expired_task_ids:
            return
        with SessionLocal() as db:
            repo = AutomationRepository(db)
            for task_id in expired_task_ids:
                task = repo.get_task(task_id)
                if (
                    task is None
                    or task.run_id != run_id
                    or task.status != "review_pending"
                ):
                    self._review_deadlines.pop(task_id, None)
                    continue
                repo.update_status(task, "skipped")
                self._log(
                    db,
                    run_id,
                    "review_timeout",
                    "review timeout, auto skipped",
                    task_id=task.task_id,
                    school_name=task.school_name,
                    level="WARN",
                )
                self._review_deadlines.pop(task_id, None)

    async def select_tasks(self, run_id: str, task_ids: list[str]) -> int:
        self._expire_review_timeouts(run_id)
        with SessionLocal() as db:
            repo = AutomationRepository(db)
            count = repo.select_tasks(run_id, task_ids)
            self._log(db, run_id, "select", f"selected tasks: {count}")
            return count

    async def delete_tasks(self, run_id: str, task_ids: list[str]) -> int:
        self._expire_review_timeouts(run_id)
        with SessionLocal() as db:
            repo = AutomationRepository(db)
            count = repo.delete_tasks(run_id, task_ids)
            self._log(db, run_id, "delete", f"deleted tasks: {count}")
            return count

    async def start_grab(self, run_id: str, limit: int = 0) -> int:
        async with self._run_lock:
            run = self.get_run(run_id)
            self._expire_review_timeouts(run_id)
            run.state = "running"
            done = 0
            with SessionLocal() as db:
                repo = AutomationRepository(db)
                rows = repo.list_by_status(run_id, ["selected"])
                if limit > 0:
                    rows = rows[:limit]

            grouped: dict[str, list[str]] = {}
            for row in rows:
                grouped.setdefault(row.school_name or "", []).append(row.task_id)

            for school_name, task_ids in grouped.items():
                for task_id in task_ids:
                    with SessionLocal() as db:
                        repo = AutomationRepository(db)
                        task = repo.get_task(task_id)
                        if task is None:
                            continue
                        self._check_stopped(run)
                        ok = await self._browser.grab_task(run_id, task.task_id)
                        if not ok:
                            repo.update_task_content(
                                task,
                                error_code="grab_failed",
                                error_message="grab failed",
                            )
                            continue
                        repo.update_status(task, "grabbed")
                        done += 1
                        self._log(
                            db,
                            run_id,
                            "grab",
                            "task grabbed",
                            task_id=task.task_id,
                            school_name=school_name,
                        )

            with SessionLocal() as db:
                self._log(db, run_id, "grab", f"grabbed tasks: {done}")
            run.state = "idle"
            return done

    async def _invoke_existing_workflow(self, image_url: str) -> str:
        payload = {"image_url": image_url}
        async with httpx.AsyncClient(timeout=180) as client:
            create_resp = await client.post(
                f"{self._workflow_api_base}/api/tasks", json=payload
            )
            create_resp.raise_for_status()
            task_id = create_resp.json()["task_id"]

            for _ in range(180):
                detail_resp = await client.get(
                    f"{self._workflow_api_base}/api/tasks/{task_id}"
                )
                detail_resp.raise_for_status()
                detail = detail_resp.json()
                if detail.get("state") in {
                    "completed",
                    "failed",
                    "manual",
                    "cancelled",
                }:
                    if detail.get("state") != "completed":
                        raise RuntimeError(
                            detail.get("error_code") or "workflow failed"
                        )
                    return detail.get("final_result") or ""
                await asyncio.sleep(1)

        raise TimeoutError("workflow timeout")

    async def start_solve(self, run_id: str, limit: int = 0) -> int:
        async with self._run_lock:
            run = self.get_run(run_id)
            self._expire_review_timeouts(run_id)
            run.state = "running"
            done = 0
            with SessionLocal() as db:
                repo = AutomationRepository(db)
                rows = repo.list_by_status(run_id, ["grabbed"])
                if limit > 0:
                    rows = rows[:limit]

            grouped: dict[str, list[str]] = {}
            for row in rows:
                grouped.setdefault(row.school_name or "", []).append(row.task_id)

            for _, task_ids in grouped.items():
                for task_id in task_ids:
                    with SessionLocal() as db:
                        repo = AutomationRepository(db)
                        task = repo.get_task(task_id)
                        if task is None:
                            continue
                        self._check_stopped(run)
                        run.current_task_id = task.task_id
                        repo.update_status(task, "solving")

                    try:
                        final_markdown = await self._invoke_existing_workflow(
                            task.topic_image_url or ""
                        )
                        analysis_md, extension = self._renderer.split_answer(
                            final_markdown
                        )
                        image_path = self._renderer.save_analysis_snapshot(analysis_md)

                        with SessionLocal() as db:
                            repo = AutomationRepository(db)
                            task = repo.get_task(task_id)
                            if task is None:
                                continue
                            self._check_stopped(run)
                            repo.update_task_content(
                                task,
                                final_markdown=final_markdown,
                                analysis_markdown=analysis_md,
                                extension_text=extension,
                            )

                        await self._browser.write_solution(
                            run_id,
                            task_id,
                            image_path,
                            extension,
                        )

                        with SessionLocal() as db:
                            repo = AutomationRepository(db)
                            task = repo.get_task(task_id)
                            if task is None:
                                continue
                            repo.update_status(task, "filled")
                            repo.update_status(task, "review_pending")
                            self._review_deadlines[task.task_id] = (
                                time.monotonic() + self._review_timeout_seconds
                            )
                            self._log(
                                db,
                                run_id,
                                "solve",
                                "task solved and waiting review",
                                task_id=task.task_id,
                                school_name=task.school_name,
                            )
                        done += 1
                    except asyncio.CancelledError:
                        with SessionLocal() as db:
                            repo = AutomationRepository(db)
                            task = repo.get_task(task_id)
                            if task and task.status != "submitted":
                                validate_transition(task.status, "stopped")
                                task.status = "stopped"
                                db.commit()
                        raise
                    except Exception as exc:
                        with SessionLocal() as db:
                            repo = AutomationRepository(db)
                            task = repo.get_task(task_id)
                            if task:
                                try:
                                    repo.update_status(task, "solve_failed")
                                except Exception:
                                    task.status = "solve_failed"
                                    db.commit()
                                repo.update_task_content(
                                    task,
                                    error_code="solve_failed",
                                    error_message=str(exc),
                                )
                                self._log(
                                    db,
                                    run_id,
                                    "solve",
                                    f"task solve failed: {exc}",
                                    task_id=task.task_id,
                                    school_name=task.school_name,
                                    level="ERROR",
                                )
                    finally:
                        run.current_task_id = None

            run.state = "idle"
            return done

    async def save_review(self, task_id: str, analysis_text: str, extension_text: str):
        with SessionLocal() as db:
            repo = AutomationRepository(db)
            task = repo.get_task(task_id)
            if not task:
                raise ValueError("task not found")
            if task.status != "review_pending":
                raise ValueError("task is not in review_pending")
            repo.update_task_content(
                task,
                analysis_edited=analysis_text,
                extension_edited=extension_text,
            )
            repo.update_status(task, "ready_to_submit")
            self._review_deadlines.pop(task.task_id, None)
            self._log(
                db,
                task.run_id,
                "review",
                "review saved",
                task_id=task.task_id,
                school_name=task.school_name,
            )
            return task

    async def confirm_submit(self, task_id: str):
        async with self._submit_lock:
            with SessionLocal() as db:
                repo = AutomationRepository(db)
                task = repo.get_task(task_id)
                if not task:
                    raise ValueError("task not found")
                if task.status != "ready_to_submit":
                    raise ValueError("task is not in ready_to_submit")
                repo.update_status(task, "submitting")

            ok = await self._browser.submit_task(task.run_id, task_id)

            with SessionLocal() as db:
                repo = AutomationRepository(db)
                task = repo.get_task(task_id)
                if not task:
                    raise ValueError("task not found")
                if ok:
                    repo.update_status(task, "submitted")
                    self._log(
                        db,
                        task.run_id,
                        "submit",
                        "submitted",
                        task_id=task.task_id,
                        school_name=task.school_name,
                    )
                else:
                    repo.update_status(task, "failed_submit")
                    self._log(
                        db,
                        task.run_id,
                        "submit",
                        "failed submit",
                        task_id=task.task_id,
                        school_name=task.school_name,
                        level="ERROR",
                    )
                return task

    async def pause(self, run_id: str) -> None:
        run = self.get_run(run_id)
        run.state = "paused"

    async def resume(self, run_id: str) -> None:
        run = self.get_run(run_id)
        run.state = "running"

    async def stop(self, run_id: str) -> None:
        run = self.get_run(run_id)
        run.state = "stopped"
        run.stop_event = run.stop_event or asyncio.Event()
        run.stop_event.set()
        if run.current_job and not run.current_job.done():
            run.current_job.cancel()
            try:
                await run.current_job
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await self._browser.stop_session(run_id)

    def list_tasks(
        self,
        run_id: str,
        status: str | None,
        school: str | None,
        page: int,
        page_size: int,
    ):
        self._expire_review_timeouts(run_id)
        with SessionLocal() as db:
            repo = AutomationRepository(db)
            return repo.list_tasks(
                run_id=run_id,
                status=status,
                school=school,
                page=page,
                page_size=page_size,
            )

    def list_logs(self, run_id: str, task_id: str | None, limit: int):
        with SessionLocal() as db:
            repo = AutomationRepository(db)
            return repo.list_logs(run_id=run_id, task_id=task_id, limit=limit)


automation_service = AutomationService()
