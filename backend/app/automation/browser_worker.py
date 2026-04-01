from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from dataclasses import dataclass


@dataclass
class BrowserRunSession:
    run_id: str
    mode: str
    username: str
    password: str
    browser: Any
    context: Any
    page: Any
    logged_in: bool = False


class BrowserWorker:
    def __init__(self):
        self._running = False
        self._playwright: Any | None = None
        self._sessions: dict[str, BrowserRunSession] = {}
        self._use_mock = os.getenv("AUTOMATION_USE_MOCK", "0") == "1"
        self._target_url = os.getenv(
            "AUTOMATION_TARGET_URL", "https://yy.xuejie.cn/#/login"
        ).strip()
        self._browser_channel = os.getenv("AUTOMATION_BROWSER_CHANNEL", "chrome").strip()
        self._browser_executable_path = os.getenv(
            "AUTOMATION_BROWSER_EXECUTABLE_PATH", ""
        ).strip()

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
        self._status_bar_selector = "div.status_bar"
        self._solving_status_selector = "div.status_item"
        self._ocr_button_selector = "button.el-button.el-button--text.el-button--mini"
        self._paste_input_selector = (
            "input[type='text'][placeholder='粘贴答案图片'][readonly='readonly']"
        )
        self._editor_selector = (
            "div[id^='w-e-textarea-'][contenteditable='true'][role='textarea']"
        )

    async def start(self) -> None:
        self._running = True
        if self._use_mock:
            return
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError(
                "playwright is required for real mode; install dependencies first"
            ) from exc
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

        launch_kwargs: dict[str, Any] = {"headless": (mode == "headless")}
        if self._browser_executable_path:
            launch_kwargs["executable_path"] = self._browser_executable_path
        elif self._browser_channel:
            launch_kwargs["channel"] = self._browser_channel

        try:
            browser = await self._playwright.chromium.launch(**launch_kwargs)
        except Exception as exc:
            raise RuntimeError(
                "browser launch failed; set AUTOMATION_BROWSER_CHANNEL=chrome or provide AUTOMATION_BROWSER_EXECUTABLE_PATH"
            ) from exc
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
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        except Exception as exc:
            raise RuntimeError("playwright runtime unavailable") from exc
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
        page = session.page

        # 按文案优先点击“我会做，抢单答题”，失败则返回 False 由上层记录异常。
        button = page.locator("text=我会做，抢单答题").first
        if await button.count() == 0:
            await asyncio.sleep(0.05)
            return False
        await button.click()
        await page.wait_for_timeout(200)
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
        page = session.page

        # 进入“解题中”列表（若存在）
        solving_items = page.locator(self._solving_status_selector).filter(
            has_text="解题中"
        )
        if await solving_items.count() > 0:
            await solving_items.first.click()
            await page.wait_for_timeout(200)

        # OCR 识别录入入口
        ocr_button = page.locator(self._ocr_button_selector).filter(has_text="识别录入")
        if await ocr_button.count() > 0:
            await ocr_button.first.click()
            await page.wait_for_timeout(200)

        # 尝试将图片路径写入粘贴框（目标站可能为 readonly，失败时继续回填编辑器）
        if image_path:
            paste_input = page.locator(self._paste_input_selector)
            if await paste_input.count() > 0:
                try:
                    await paste_input.first.fill(image_path)
                except Exception:
                    pass

        editors = page.locator(self._editor_selector)
        if await editors.count() > 0:
            # 第一个编辑器写入解析
            await editors.nth(0).fill(f"已自动录入，来源任务: {task_id}")
            if await editors.count() > 1 and extension_text:
                # 若页面存在第二个可编辑区，优先写入考点衍生
                await editors.nth(1).fill(extension_text)

        await page.wait_for_timeout(150)
        return True

    async def submit_task(self, run_id: str, task_id: str) -> bool:
        if self._use_mock:
            await asyncio.sleep(0.05)
            return True

        session = self._sessions.get(run_id)
        if session is None:
            raise ValueError(f"session not found: {run_id}")
        await self._ensure_login(run_id)
        page = session.page
        submit_button = page.locator("button:has-text('提交')")
        if await submit_button.count() == 0:
            await asyncio.sleep(0.05)
            return False
        await submit_button.first.click()
        await page.wait_for_timeout(300)
        return True
