from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.automation.api_client import XuejieApiClient
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
        self._api_client: XuejieApiClient | None = None
        self._renderer = MarkdownRenderer()
        self._logs = AutomationLoggingStore()
        self._review_deadlines: dict[str, float] = {}
        self._review_timeout_seconds = 600
        self._run_lock = asyncio.Lock()
        self._submit_lock = asyncio.Lock()
        self._workflow_api_base = os.getenv(
            "AUTOMATION_WORKFLOW_API_BASE", "http://127.0.0.1:8080"
        ).rstrip("/")
        self._credentials: tuple[str, str] | None = None  # (username, password)

    def _build_workflow_create_payload(
        self,
        image_url: str = "",
        image_urls: list[str] | None = None,
        question_text: str = "",
    ) -> dict:
        """
        构建工作流创建 payload，与智能解析流程保持一致的字段结构。
        image_urls: 图片 URL 列表（与 image_url 二选一，优先用 image_urls）
        """
        payload: dict[str, str | list] = {}
        if image_urls:
            payload["image_urls"] = image_urls
            if image_url and image_url not in image_urls:
                payload["image_url"] = image_url
        elif image_url:
            payload["image_url"] = image_url
        if question_text:
            payload["question_text"] = question_text
        return payload

    async def start_session(self, req: StartSessionReq) -> RunRuntime:
        run = RunRuntime(
            run_id=f"run_{uuid.uuid4().hex[:10]}",
            mode=req.mode,
            state="idle",
            stop_event=asyncio.Event(),
        )
        async with self._lock:
            self._runs[run.run_id] = run

        # headed 模式：启动浏览器，用户手动登录，获取浏览器凭证给 API
        # headless 模式：直接用 API 登录
        if req.mode == "headed":
            # 启动浏览器（headed 模式）
            await self._browser.start_session(
                run_id=run.run_id,
                username=req.username,
                password=req.password,
                mode=req.mode,
            )
            # 从浏览器获取 token 给 API 客户端（避免双登录冲突）
            session = self._browser._sessions.get(run.run_id)
            if session and session.page:
                # 尝试从浏览器 localStorage 获取 token
                try:
                    token = await session.page.evaluate(
                        "() => localStorage.getItem('token') || sessionStorage.getItem('token')"
                    )
                    if token:
                        self._credentials = (req.username, req.password)
                        self._api_client = XuejieApiClient()
                        self._api_client._token = token
                        # 不再调用 login，直接用浏览器里的 token
                        return run
                except Exception:
                    pass
            # 如果获取 token 失败，尝试手动用凭证登录
            self._credentials = (req.username, req.password)
            self._api_client = XuejieApiClient()
            await self._api_client.login(req.username, req.password)
        else:
            # headless 模式：直接用 API 登录
            self._credentials = (req.username, req.password)
            self._api_client = XuejieApiClient()
            await self._api_client.login(req.username, req.password)
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

    async def start_scan(self, run_id: str, school_id: int | None = None) -> None:
        async with self._run_lock:
            run = self.get_run(run_id)
            run.state = "running"
            run.stop_event = run.stop_event or asyncio.Event()
            self._log_runtime(run_id, "scan", "scan job started")

            if self._api_client is None:
                raise RuntimeError("API client not initialized, call start_session first")

            all_tasks = []

            # 获取所有学校列表（用于 school_id -> school_name 映射）
            schools = await self._api_client.list_schools()
            school_map = {s.get("school_id"): s.get("school_name", f"学校{s.get('school_id')}") for s in schools if s.get("school_id")}
            self._log_runtime(run_id, "scan", f"found {len(schools)} schools")

            # 指定学校时只扫描该学校，否则扫描所有学校
            if school_id:
                self._log_runtime(run_id, "scan", f"scanning school_id={school_id} ({school_map.get(school_id, '')})")
                target_school_ids = [school_id]
            else:
                target_school_ids = list(school_map.keys())

            # 遍历每个学校获取待解题任务（API 需要指定 school_id 才能返回待解题）
            import random
            for idx, sid in enumerate(target_school_ids):
                self._check_stopped(run)
                page = 1
                pagesize = 50
                while True:
                    tasks = await self._api_client.list_pending_tasks(
                        school_id=sid, page=page, pagesize=pagesize
                    )
                    if not tasks:
                        break
                    all_tasks.extend(tasks)
                    if len(tasks) < pagesize:
                        break
                    page += 1

                # 每个学校请求后随机延迟 0.5~2.5 秒，避免触发风控
                delay = random.uniform(0.5, 2.5)
                self._log_runtime(run_id, "scan", f"school {idx+1}/{len(school_ids)} done, sleep {delay:.1f}s")
                await asyncio.sleep(delay)

            self._log_runtime(run_id, "scan", f"API returned {len(all_tasks)} tasks")

            rows = []
            for t in all_tasks:
                task_id = str(t.get("id", ""))
                # 从 paperInfo.school_id 查找学校名称
                paper_info = t.get("paperInfo") or {}
                sid = paper_info.get("school_id", 0)
                school_name = school_map.get(sid, f"学校{sid}")

                # 优先用 topic_text 前200字符作为题目标题
                topic_text = t.get("topic_text") or ""
                topic_title = topic_text[:200] if topic_text else "无标题"
                topic_title = str(topic_title).replace("\n", " ").strip()[:200]

                # 收集所有图片 URL（topic_img + topic_img2 + topic_img3）
                raw_urls = [
                    t.get("topic_img") or "",
                    t.get("topic_img2") or "",
                    t.get("topic_img3") or "",
                ]
                all_urls = [u.strip() for u in raw_urls if u.strip()]
                import json as _json
                topic_image_url = _json.dumps(all_urls) if len(all_urls) > 1 else (all_urls[0] if all_urls else "")
                rows.append(
                    {
                        "task_id": task_id,
                        "school_name": school_name,
                        "topic_title": topic_title,
                        "topic_image_url": topic_image_url,
                        "topic_text": topic_text,
                    }
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
                        topic_text=row.get("topic_text"),
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

    async def trigger_scan(self, run_id: str, school_id: int | None = None) -> None:
        run = self.get_run(run_id)
        self._ensure_idle(run)
        run.stop_event = asyncio.Event()
        run.current_job = asyncio.create_task(
            self._run_job(run_id, "scan", self.start_scan(run_id, school_id))
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
                if task is None or task.status != "review_pending":
                    self._review_deadlines.pop(task_id, None)
                    continue
                repo.update_status(task, "skipped")
                self._log(
                    db,
                    task.run_id,
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

            if self._api_client is None:
                raise RuntimeError("API client not initialized")

            with SessionLocal() as db:
                repo = AutomationRepository(db)
                rows = repo.list_by_status(run_id, ["selected"])
                if limit > 0:
                    rows = rows[:limit]

            for row in rows:
                with SessionLocal() as db:
                    repo = AutomationRepository(db)
                    task = repo.get_task(row.task_id)
                    if task is None:
                        continue
                    self._check_stopped(run)
                    task.run_id = run_id
                    db.commit()

                try:
                    await self._api_client.grab_task(int(row.task_id))
                    with SessionLocal() as db:
                        repo = AutomationRepository(db)
                        task = repo.get_task(row.task_id)
                        if task:
                            repo.update_status(task, "grabbed")
                            self._log(
                                db,
                                run_id,
                                "grab",
                                "task grabbed via API",
                                task_id=task.task_id,
                                school_name=task.school_name,
                            )
                    done += 1
                except Exception as exc:
                    with SessionLocal() as db:
                        repo = AutomationRepository(db)
                        task = repo.get_task(row.task_id)
                        if task:
                            repo.update_task_content(
                                task,
                                error_code="grab_failed",
                                error_message=str(exc),
                            )
                            self._log(
                                db,
                                run_id,
                                "grab",
                                f"task grab failed: {exc}",
                                task_id=task.task_id,
                                school_name=task.school_name,
                                level="WARN",
                            )

            with SessionLocal() as db:
                self._log(db, run_id, "grab", f"grabbed tasks: {done}")
            run.state = "idle"
            return done

    async def _download_image_as_data_url(self, image_url: str) -> str:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").split(";")[0]
            content_type = content_type.strip().lower()
            if not content_type.startswith("image/"):
                content_type = "image/png"
            encoded = base64.b64encode(resp.content).decode("ascii")
            return f"data:{content_type};base64,{encoded}"

    async def _ensure_data_url(self, image_url: str) -> str:
        source = (image_url or "").strip()
        if source.startswith("data:image/"):
            return source
        return await self._download_image_as_data_url(source)

    async def _invoke_existing_workflow(
        self,
        image_urls: list[str],
        question_text: str = "",
        on_log=None,
    ) -> dict:
        """
        调用工作流解题，image_urls 中的每张图片都会被转换为 base64 data URL，
        与智能解析流程完全一致。
        """
        def emit(level: str, message: str) -> None:
            if on_log is None:
                return
            on_log(level, "solve.workflow", message)

        async def _run_once(
            payload_image_urls: list[str], payload_question_text: str
        ) -> dict:
            payload = self._build_workflow_create_payload(
                image_urls=payload_image_urls,
                question_text=payload_question_text,
            )
            async with httpx.AsyncClient(timeout=180) as client:
                create_resp = await client.post(
                    f"{self._workflow_api_base}/api/tasks", json=payload
                )
                create_resp.raise_for_status()
                task_id = create_resp.json()["task_id"]
                emit("INFO", f"workflow task created: {task_id}, images={len(payload_image_urls)}")

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
                            error_code = detail.get("error_code") or "workflow failed"
                            emit(
                                "WARN",
                                f"workflow task failed: {task_id}; error={error_code}",
                            )
                            raise RuntimeError(error_code)
                        emit("INFO", f"workflow task completed: {task_id}")
                        return {
                            "workflow_task_id": task_id,
                            "final_result": detail.get("final_result") or "",
                            "answer_preview": detail.get("answer_preview") or "",
                        }
                    await asyncio.sleep(1)

            raise TimeoutError("workflow timeout")

        # 所有图片 URL 转为 base64
        data_urls = []
        for url in image_urls:
            data_url = await self._ensure_data_url(url)
            data_urls.append(data_url)

        return await _run_once(data_urls, question_text)

    async def start_solve(self, run_id: str, limit: int = 0) -> int:
        async with self._run_lock:
            run = self.get_run(run_id)
            self._expire_review_timeouts(run_id)
            run.state = "running"
            done = 0
            with SessionLocal() as db:
                repo = AutomationRepository(db)
                rows = repo.list_by_status(run_id, ["grabbed", "solve_failed"])
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
                        task.run_id = run_id
                        repo.update_status(task, "solving")

                    try:

                        def _solve_logger(level: str, step: str, message: str) -> None:
                            self._log_runtime(
                                run_id,
                                step,
                                message,
                                level=level,
                                task_id=task.task_id,
                                school_name=task.school_name,
                            )

                        # 抢单已由 API 在 start_grab 完成，无需再调用浏览器 prepare_solve_task
                        # topic_image_url 可能是 JSON 数组（多图）或单个 URL
                        import json as _json

                        image_urls_for_workflow: list[str] = []
                        if task.topic_image_url:
                            try:
                                parsed = _json.loads(task.topic_image_url)
                                if isinstance(parsed, list):
                                    image_urls_for_workflow = [u for u in parsed if u]
                                else:
                                    image_urls_for_workflow = [task.topic_image_url]
                            except Exception:
                                # 不是 JSON，当作单图处理
                                image_urls_for_workflow = [task.topic_image_url]
                        if not image_urls_for_workflow:
                            raise RuntimeError("missing source image url for workflow")

                        # topic_text 来自网站 API 的 OCR 识别结果，直接透传给工作流增强解题
                        question_text = task.topic_text or ""

                        workflow_result = await self._invoke_existing_workflow(
                            image_urls_for_workflow,
                            question_text=question_text,
                            on_log=_solve_logger,
                        )
                        workflow_task_id = workflow_result.get("workflow_task_id") or ""
                        final_markdown = workflow_result.get("final_result") or ""
                        answer_preview = workflow_result.get("answer_preview") or ""
                        analysis_md, extension = self._renderer.split_answer(
                            final_markdown
                        )
                        render_markdown = (answer_preview or analysis_md).strip()
                        self._renderer.save_analysis_snapshot(render_markdown)

                        _solve_logger(
                            "INFO",
                            "solve.workflow",
                            f"workflow detail loaded by task_id={workflow_task_id}; answer_preview_len={len(answer_preview)}",
                        )

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

                        # 不再调用 browser.write_solution（已由 API 抢单，内容存 DB 即可）
                        # actual submission happens in confirm_submit via API
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
        """
        仅写入答案内容（不提交），提交由用户到 xuejie 网站手动完成。
        save_answer 使用 status=2（草稿）而非 status=6（提交审核），
        is_dev_submit=0 表示非主动提交。
        """
        async with self._submit_lock:
            with SessionLocal() as db:
                repo = AutomationRepository(db)
                task = repo.get_task(task_id)
                if not task:
                    raise ValueError("task not found")
                if task.status != "ready_to_submit":
                    raise ValueError("task is not in ready_to_submit")

            # 取用户编辑后的内容（若有），否则用 AI 生成的原始内容
            analysis_text = (
                task.analysis_edited
                or task.analysis_markdown
                or ""
            )
            extension_text = task.extension_edited or task.extension_text or ""

            # 将 markdown 转为 HTML
            topic_html, answer_html = self._render_answer_to_html(
                task.final_markdown or "", analysis_text, extension_text
            )
            topic_text = analysis_text.strip()

            try:
                if self._api_client is None:
                    raise RuntimeError("API client not initialized")
                # status=2: 仅保存草稿，不触发网站提交
                # is_dev_submit=0: 非主动提交
                await self._api_client.save_answer(
                    task_id=int(task_id),
                    topic=topic_html,
                    answer=answer_html,
                    topic_text=topic_text,
                    exam_point=extension_text,
                    status=2,
                    is_dev_submit=0,
                )
                with SessionLocal() as db:
                    repo = AutomationRepository(db)
                    task = repo.get_task(task_id)
                    if task:
                        # 本地状态改为 written（已写入，等待用户到网站手动提交）
                        repo.update_status(task, "written")
                        self._log(
                            db,
                            task.run_id,
                            "submit",
                            "written to website (awaiting manual submit by user)",
                            task_id=task.task_id,
                            school_name=task.school_name,
                        )
            except Exception as exc:
                with SessionLocal() as db:
                    repo = AutomationRepository(db)
                    task = repo.get_task(task_id)
                    if task:
                        repo.update_status(task, "failed_write")
                        repo.update_task_content(
                            task,
                            error_code="write_failed",
                            error_message=str(exc),
                        )
                        self._log(
                            db,
                            task.run_id,
                            "submit",
                            f"write failed: {exc}",
                            task_id=task.task_id,
                            school_name=task.school_name,
                            level="ERROR",
                        )
            return task

    def _render_answer_to_html(
        self, final_markdown: str, analysis_text: str, extension_text: str
    ) -> tuple[str, str]:
        """
        将 markdown 内容转换为 API 所需的 HTML 格式。
        topic_html: 题目部分（去掉了答案标记之前的内容）
        answer_html: 答案部分（【正解】/【解析】标记起至结束）
        """
        # 题目 = final_markdown 中答案标记之前的内容
        answer_marker = final_markdown.find("【正解】")
        if answer_marker < 0:
            answer_marker = final_markdown.find("【解析】")
        if answer_marker >= 0:
            topic_section = final_markdown[:answer_marker].strip()
        else:
            topic_section = final_markdown.strip()

        # 答案 = 【正解】/【解析】起至结束
        if answer_marker >= 0:
            answer_section = final_markdown[answer_marker:].strip()
        else:
            answer_section = ""

        topic_html = f"<p>{topic_section.replace(chr(10), '<br>')}</p>"
        answer_html = f"<p>{answer_section.replace(chr(10), '<br>')}</p>"
        return topic_html, answer_html

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

    async def list_schools(self) -> list[dict]:
        """获取学校列表"""
        if self._api_client is None:
            return []
        return await self._api_client.list_schools()

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
