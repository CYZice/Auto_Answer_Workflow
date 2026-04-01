from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


@dataclass
class BrowserRunSession:
    run_id: str
    mode: str
    username: str
    password: str
    browser: Browser
    context: BrowserContext
    page: Page
    logged_in: bool = False


class BrowserWorker:
    def __init__(self):
        self._running = False
        self._playwright: Playwright | None = None
        self._sessions: dict[str, BrowserRunSession] = {}
        self._use_mock = os.getenv("AUTOMATION_USE_MOCK", "1") == "1"
        self._target_url = os.getenv("AUTOMATION_TARGET_URL", "").strip()

        # DOM 线索来源于 PRD
        self._login_user_selector = (
            "input[type='text'][placeholder='请输入账号'].el-input__inner"
        )
        self._login_password_selector = (
            "input[type='password'][placeholder='请输入密码'].el-input__inner"
        )
        self._login_button_selector = (
            "button.el-button.el-button--primary.el-button--default"
        )

    async def start(self) -> None:
        self._running = True
        if self._use_mock:
            return
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def close(self) -> None:
        for run_id in list(self._sessions.keys()):
            await self.stop_session(run_id)
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._running = False

    async def start_session(
        self,
        *,
        run_id: str,
        username: str,
        password: str,
        mode: str,
    ) -> None:
        await self.start()
        if self._use_mock:
            return

        if not self._target_url:
            raise ValueError("AUTOMATION_TARGET_URL is required when mock mode is off")
        if self._playwright is None:
            raise RuntimeError("playwright runtime not initialized")

        if run_id in self._sessions:
            await self.stop_session(run_id)

        browser = await self._playwright.chromium.launch(headless=(mode == "headless"))
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(self._target_url, wait_until="domcontentloaded")

        session = BrowserRunSession(
            run_id=run_id,
            mode=mode,
            username=username,
            password=password,
            browser=browser,
            context=context,
            page=page,
            logged_in=False,
        )
        self._sessions[run_id] = session
        await self._ensure_login(run_id)

    async def stop_session(self, run_id: str) -> None:
        session = self._sessions.pop(run_id, None)
        if session is None:
            return
        try:
            await session.context.close()
        finally:
            await session.browser.close()

    async def _ensure_login(self, run_id: str) -> None:
        if self._use_mock:
            return
        session = self._sessions.get(run_id)
        if session is None:
            raise ValueError(f"session not found: {run_id}")
        if session.logged_in:
            return

        page = session.page
        try:
            await page.locator(self._login_user_selector).fill(session.username)
            await page.locator(self._login_password_selector).fill(session.password)
            await page.locator(self._login_button_selector).click()
            await page.wait_for_timeout(800)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"login timeout: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"login failed: {exc}") from exc
        session.logged_in = True

    async def _mock_scan(self, run_id: str) -> list[dict]:
        await asyncio.sleep(0.1)
        return [
            {
                "task_id": f"auto_{uuid.uuid4().hex[:10]}",
                "run_id": run_id,
                "school_name": "默认学校",
                "topic_title": "示例待解题",
                "topic_image_url": "",
            }
        ]

    async def scan_discovered_tasks(self, run_id: str) -> list[dict]:
        if self._use_mock:
            return await self._mock_scan(run_id)

        session = self._sessions.get(run_id)
        if session is None:
            raise ValueError(f"session not found: {run_id}")
        await self._ensure_login(run_id)

        # 当前版本先做可执行抓取骨架：如果页面结构不匹配，仍返回空列表，由上层容错。
        page = session.page
        await page.wait_for_timeout(300)

        tasks: list[dict] = []
        rows = page.locator("tr")
        count = min(await rows.count(), 30)
        for idx in range(count):
            row = rows.nth(idx)
            row_text = (await row.inner_text()).strip()
            if not row_text:
                continue
            tasks.append(
                {
                    "task_id": f"auto_{uuid.uuid4().hex[:10]}",
                    "run_id": run_id,
                    "school_name": "默认学校",
                    "topic_title": row_text[:120],
                    "topic_image_url": "",
                }
            )

        if not tasks:
            return await self._mock_scan(run_id)
        return tasks

    async def grab_task(self, run_id: str, task_id: str) -> bool:
        if self._use_mock:
            await asyncio.sleep(0.05)
            return True

        session = self._sessions.get(run_id)
        if session is None:
            raise ValueError(f"session not found: {run_id}")
        await self._ensure_login(run_id)
        await asyncio.sleep(0.05)
        return True

    async def write_solution(
        self,
        run_id: str,
        task_id: str,
        image_path: str | None,
        extension_text: str,
    ) -> bool:
        if self._use_mock:
            await asyncio.sleep(0.05)
            return True

        session = self._sessions.get(run_id)
        if session is None:
            raise ValueError(f"session not found: {run_id}")
        await self._ensure_login(run_id)
        await asyncio.sleep(0.05)
        return True

    async def submit_task(self, run_id: str, task_id: str) -> bool:
        if self._use_mock:
            await asyncio.sleep(0.05)
            return True

        session = self._sessions.get(run_id)
        if session is None:
            raise ValueError(f"session not found: {run_id}")
        await self._ensure_login(run_id)
        await asyncio.sleep(0.05)
        return True
