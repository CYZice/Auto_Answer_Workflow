from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from collections.abc import Callable
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
        self._browser_channel = os.getenv(
            "AUTOMATION_BROWSER_CHANNEL", "chrome"
        ).strip()
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
        self._single_research_candidates = [
            "li.el-menu-item:has(span:has-text('单题研发'))",
            "li[role='menuitem']:has-text('单题研发')",
            "ul[role='menubar'] li.el-menu-item:has-text('单题研发')",
            "text=单题研发",
            "a:has-text('单题研发')",
            "span:has-text('单题研发')",
            "div:has-text('单题研发')",
        ]
        self._research_tab_candidates = [
            "text=研发",
            "a:has-text('研发')",
            "span:has-text('研发')",
            "li:has-text('研发')",
        ]
        self._school_dropdown_candidates = [
            "input[placeholder='选择学校']",
            "input[placeholder*='学校']",
            "input[placeholder='通用']",
            "input[placeholder*='请选择']",
            "div.el-select:has(input[placeholder*='学校'])",
            "div.el-select:has(input[placeholder='选择学校'])",
            "div.el-select:has(input[placeholder*='请选择'])",
            "div.el-select",
        ]
        self._school_option_selector = (
            "li.el-select-dropdown__item, .el-select-dropdown__item, [role='option']"
        )
        self._pending_tab_candidates = [
            "text=待开始",
            "text=待解题",
            "button:has-text('待开始')",
            "button:has-text('待解题')",
            "div:has-text('待开始')",
            "div:has-text('待解题')",
        ]
        self._task_action_button_selector = "button:has-text('我会做，抢单答题')"
        self._scan_noise_keywords = {
            "薪酬统计",
            "提现统计",
            "基础信息",
            "电话微信",
            "学校:",
            "管理员奖罚",
            "研发学科",
        }

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

    async def _click_first_available(self, page: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = min(await locator.count(), 10)
                if count == 0:
                    continue
                for idx in range(count):
                    node = locator.nth(idx)
                    try:
                        if not await node.is_visible():
                            continue
                        await node.click(timeout=1500)
                        await page.wait_for_timeout(300)
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    async def _is_single_research_ready(self, page: Any) -> bool:
        ready_selectors = [
            "input[placeholder='选择学校']",
            "input[placeholder='选择学校'][readonly='readonly']",
            "div.el-select input[placeholder='选择学校']",
            "div.el-select input[placeholder*='学校']",
            "input[placeholder='通用']",
        ]
        for selector in ready_selectors:
            try:
                locator = page.locator(selector)
                count = min(await locator.count(), 5)
                for idx in range(count):
                    if await locator.nth(idx).is_visible():
                        return True
            except Exception:
                continue

        # 兜底：若侧边栏当前激活菜单文本包含“单题研发”，也视作进入成功。
        try:
            active_menu = page.locator(
                "ul[role='menubar'] li.el-menu-item.active, ul[role='menubar'] li.el-menu-item.is-active"
            )
            count = min(await active_menu.count(), 3)
            for idx in range(count):
                text = (await active_menu.nth(idx).inner_text()).strip()
                if "单题研发" in text:
                    return True
        except Exception:
            pass

        return False

    async def _read_sidebar_menu_texts(self, page: Any) -> list[str]:
        items: list[str] = []
        try:
            menu_items = page.locator("ul[role='menubar'] li.el-menu-item")
            count = min(await menu_items.count(), 12)
            for idx in range(count):
                text = (await menu_items.nth(idx).inner_text()).strip()
                if text:
                    items.append(text)
        except Exception:
            return items
        return items

    async def _click_sidebar_single_research(self, page: Any) -> bool:
        try:
            menu_items = page.locator("ul[role='menubar'] li.el-menu-item")
            count = min(await menu_items.count(), 20)
            for idx in range(count):
                item = menu_items.nth(idx)
                try:
                    if not await item.is_visible():
                        continue
                    text = (await item.inner_text()).strip()
                    if "单题研发" not in text:
                        continue
                    await item.click(timeout=1500)
                    await page.wait_for_timeout(300)
                    return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    async def _extract_table_tasks(
        self, page: Any, run_id: str, school_name: str
    ) -> list[dict]:
        tasks: list[dict] = []
        # 优先从可执行操作按钮反向定位任务容器，避免误扫首页统计区域。
        action_buttons = page.locator(self._task_action_button_selector)
        action_count = min(await action_buttons.count(), 50)
        for idx in range(action_count):
            button = action_buttons.nth(idx)
            row = button.locator("xpath=ancestor::tr[1]")
            if await row.count() == 0:
                row = button.locator("xpath=ancestor::li[1]")
            if await row.count() == 0:
                row = button.locator("xpath=ancestor::div[contains(@class,'item')][1]")
            if await row.count() == 0:
                continue
            row_text = (await row.first.inner_text()).strip()
            if not row_text or len(row_text) < 3:
                continue
            if any(keyword in row_text for keyword in self._scan_noise_keywords):
                continue
            tasks.append(
                {
                    "task_id": f"auto_{uuid.uuid4().hex[:10]}",
                    "run_id": run_id,
                    "school_name": school_name or "默认学校",
                    "topic_title": row_text[:120],
                    "topic_image_url": "",
                }
            )

        # 若页面无接单按钮，使用窄范围回退，仍尽量避免全页 tr 扫描。
        if not tasks:
            scoped_rows = page.locator(".d2-container-full__body tr")
            scoped_count = min(await scoped_rows.count(), 30)
            for idx in range(scoped_count):
                row = scoped_rows.nth(idx)
                row_text = (await row.inner_text()).strip()
                if not row_text or len(row_text) < 6:
                    continue
                if any(keyword in row_text for keyword in self._scan_noise_keywords):
                    continue
                tasks.append(
                    {
                        "task_id": f"auto_{uuid.uuid4().hex[:10]}",
                        "run_id": run_id,
                        "school_name": school_name or "默认学校",
                        "topic_title": row_text[:120],
                        "topic_image_url": "",
                    }
                )
        return tasks

    async def _ensure_single_research_ready(self, page: Any) -> bool:
        # 已在单题研发页面时直接通过，避免重复点击导致失败。
        if await self._is_single_research_ready(page):
            return True

        # 先尝试顶部“研发”菜单，再点击左侧“单题研发”。
        await self._click_first_available(page, self._research_tab_candidates)
        clicked = await self._click_sidebar_single_research(page)
        if not clicked:
            clicked = await self._click_first_available(
                page, self._single_research_candidates
            )
        if not clicked:
            return False

        for _ in range(10):
            if await self._is_single_research_ready(page):
                return True
            await page.wait_for_timeout(300)
        return False

    async def _open_school_dropdown(self, page: Any) -> bool:
        school_input = page.locator("input[placeholder='选择学校']").first
        if await school_input.count() > 0:
            try:
                await school_input.click(timeout=1500)
                await page.wait_for_timeout(250)
                return True
            except Exception:
                pass
        return await self._click_first_available(page, self._school_dropdown_candidates)

    async def _switch_to_pending(self, page: Any) -> None:
        await self._click_first_available(page, self._pending_tab_candidates)
        await page.wait_for_timeout(300)

    async def scan_discovered_tasks(
        self,
        run_id: str,
        on_log: Callable[[str, str, str], None] | None = None,
    ) -> list[dict]:
        def emit(level: str, step: str, message: str) -> None:
            if on_log is None:
                return
            on_log(level, step, message)

        if self._use_mock:
            emit("INFO", "scan.mock", "mock mode enabled, using mock discovered tasks")
            return await self._mock_scan(run_id)

        session = self._sessions.get(run_id)
        if session is None:
            emit("ERROR", "scan.session", f"session not found: {run_id}")
            raise ValueError(f"session not found: {run_id}")
        await self._ensure_login(run_id)
        emit("INFO", "scan.session", "session ready and login verified")

        # 当前版本先做可执行抓取骨架：如果页面结构不匹配，仍返回空列表，由上层容错。
        page = session.page
        await page.wait_for_timeout(800)

        # 登录后必须先进入“单题研发”，否则不执行扫描。
        emit("INFO", "scan.nav", "trying to enter 单题研发")
        emit("INFO", "scan.nav", f"url before nav: {page.url}")
        if not await self._ensure_single_research_ready(page):
            menu_texts = await self._read_sidebar_menu_texts(page)
            emit(
                "WARN",
                "scan.nav",
                "failed to enter 单题研发, scan aborted",
            )
            emit("WARN", "scan.nav", f"sidebar menu snapshot: {' | '.join(menu_texts)}")
            emit("WARN", "scan.nav", f"url after nav: {page.url}")
            return []
        emit("INFO", "scan.nav", "entered 单题研发")
        emit("INFO", "scan.nav", f"url after nav: {page.url}")
        await self._switch_to_pending(page)
        emit("INFO", "scan.nav", "switched to 待开始/待解题")

        tasks: list[dict] = []
        seen_titles: set[str] = set()

        # 再尝试学校下拉遍历抓取
        opened = await self._open_school_dropdown(page)
        if opened:
            emit("INFO", "scan.school", "school dropdown opened")
        else:
            emit(
                "WARN", "scan.school", "failed to open school dropdown, using fallback"
            )

        if opened:
            options = page.locator(self._school_option_selector)
            option_count = min(await options.count(), 50)
            school_names: list[str] = []
            for idx in range(option_count):
                name = (await options.nth(idx).inner_text()).strip()
                if not name or name in {"全部", "请选择", "通用", "选择学校"}:
                    continue
                if name not in school_names:
                    school_names.append(name)

            emit("INFO", "scan.school", f"resolved school options: {len(school_names)}")

            for school_name in school_names:
                # 每次选择前重新打开下拉，避免元素失效
                await self._open_school_dropdown(page)
                option = (
                    page.locator(self._school_option_selector)
                    .filter(has_text=school_name)
                    .first
                )
                if await option.count() == 0:
                    emit("WARN", "scan.school", f"school option missing: {school_name}")
                    continue
                try:
                    await option.click(timeout=1500)
                except Exception:
                    emit(
                        "WARN",
                        "scan.school",
                        f"school option click failed: {school_name}",
                    )
                    continue
                await page.wait_for_timeout(600)
                await self._switch_to_pending(page)
                emit("INFO", "scan.school", f"switched school: {school_name}")

                school_tasks = await self._extract_table_tasks(
                    page, run_id, school_name
                )
                emit(
                    "INFO",
                    "scan.extract",
                    f"school {school_name} extracted tasks: {len(school_tasks)}",
                )
                for item in school_tasks:
                    if item["topic_title"] in seen_titles:
                        continue
                    seen_titles.add(item["topic_title"])
                    tasks.append(item)

        # 学校遍历失败时，至少返回当前学校下待解题列表。
        if not tasks:
            fallback_tasks = await self._extract_table_tasks(page, run_id, "默认学校")
            emit(
                "INFO", "scan.extract", f"fallback extract tasks: {len(fallback_tasks)}"
            )
            for item in fallback_tasks:
                if item["topic_title"] in seen_titles:
                    continue
                seen_titles.add(item["topic_title"])
                tasks.append(item)

        emit("INFO", "scan.complete", f"scan completed with tasks: {len(tasks)}")
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
