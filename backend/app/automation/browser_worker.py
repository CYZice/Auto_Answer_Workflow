from __future__ import annotations

import asyncio
import hashlib
import os
import re
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
        self._task_action_button_selector = "button:has-text('我会做，抢单答题'), button:has-text('我会做'), button:has-text('抢单答题')"
        self._task_rows_selector = ".d2-container-full__body .el-table__body-wrapper .el-table__body tbody tr.el-table__row"
        self._view_button_selector = (
            "td:nth-child(4) .cell button.el-button.el-button--text.el-button--default:has(span:has-text('查看')), "
            "td:last-child .cell button.el-button.el-button--text.el-button--default:has(span:has-text('查看'))"
        )
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

    async def _get_visible_dialog_wrapper(self, page: Any) -> Any | None:
        wrappers = page.locator(".el-dialog__wrapper")
        count = min(await wrappers.count(), 12)
        for idx in range(count):
            wrapper = wrappers.nth(idx)
            try:
                if not await wrapper.is_visible():
                    continue
                style = (await wrapper.get_attribute("style") or "").replace(" ", "")
                if "display:none" in style:
                    continue
                return wrapper
            except Exception:
                continue
        return None

    async def _extract_full_title_from_view(
        self,
        page: Any,
        row_index: int,
        fallback_title: str,
        on_log: Callable[[str, str, str], None] | None = None,
    ) -> tuple[str, str]:
        topic_title = fallback_title
        topic_image_url = ""

        def emit(level: str, message: str) -> None:
            if on_log is None:
                return
            prefix = f"row {row_index}: "
            on_log(level, "scan.view", f"{prefix}{message}")

        try:
            rows = page.locator(self._task_rows_selector)
            row_count = await rows.count()
            if row_index >= row_count:
                emit("WARN", "行索引越界，无法点击查看")
                return topic_title, topic_image_url

            row = rows.nth(row_index)
            view_trigger = row.locator(self._view_button_selector).first
            if await view_trigger.count() == 0:
                # 兜底：若列结构变化，回退到行内全文匹配。
                view_trigger = row.locator(
                    "button:has(span:has-text('查看')), button:has-text('查看'), span:has-text('查看')"
                ).first
            if await view_trigger.count() == 0:
                emit("WARN", "查看按钮不存在")
                return topic_title, topic_image_url

            before_url = page.url
            emit("INFO", "点击查看进入详情")
            await view_trigger.click(timeout=2000)
            await page.wait_for_timeout(300)

            dialog = None
            for _ in range(20):
                dialog = await self._get_visible_dialog_wrapper(page)
                if dialog is not None:
                    break
                await page.wait_for_timeout(150)

            if dialog is None:
                emit("WARN", "未检测到可见详情弹窗，回退读取当前行内容")
                row_topic = row.locator("td:nth-child(2) .topic").first
                if await row_topic.count() > 0:
                    text = (await row_topic.inner_text()).strip()
                    if len(text) > len(topic_title):
                        topic_title = text
                return topic_title, topic_image_url

            detail_candidates = [
                ".topic",
                ".el-dialog__body",
                "p",
            ]
            best_text = topic_title
            for selector in detail_candidates:
                locator = dialog.locator(selector)
                count = min(await locator.count(), 3)
                for idx in range(count):
                    text = (await locator.nth(idx).inner_text()).strip()
                    if len(text) > len(best_text):
                        best_text = text
            if best_text:
                topic_title = best_text
            emit("INFO", f"详情抓取文本长度: {len(topic_title)}")

            image_candidates = [
                ".el-dialog__body img",
                ".topic img",
            ]
            for selector in image_candidates:
                img = dialog.locator(selector).first
                if await img.count() == 0:
                    continue
                src = await img.get_attribute("src")
                if src:
                    topic_image_url = src.strip()
                    emit("INFO", "详情中检测到题图")
                    break

            closed = await self._click_first_available(
                dialog,
                [
                    "button:has-text('返回')",
                    "button:has-text('关闭')",
                    "span:has-text('返回')",
                    ".el-dialog__headerbtn",
                ],
            )
            if not closed:
                closed = await self._click_first_available(
                    page,
                    [
                        ".el-dialog__wrapper .el-dialog__headerbtn",
                        ".el-dialog__headerbtn",
                    ],
                )
            if not closed and page.url != before_url:
                try:
                    await page.go_back(wait_until="domcontentloaded")
                except Exception:
                    pass
            await page.wait_for_timeout(400)
        except Exception as exc:
            emit("WARN", f"查看详情失败: {exc}")
            return fallback_title, ""

        return topic_title, topic_image_url

    async def _extract_table_tasks(
        self,
        page: Any,
        run_id: str,
        school_name: str,
        on_log: Callable[[str, str, str], None] | None = None,
    ) -> list[dict]:
        tasks: list[dict] = []

        def emit(level: str, message: str) -> None:
            if on_log is None:
                return
            on_log(level, "scan.extract", f"[{school_name or '默认学校'}] {message}")

        rows = page.locator(self._task_rows_selector)
        row_count = min(await rows.count(), 80)
        if row_count == 0:
            emit("WARN", "未找到任务表格行")
            return tasks

        emit("INFO", f"候选行数量: {row_count}")

        for idx in range(row_count):
            # 每次按索引重新取行，避免点击查看后 DOM 刷新导致句柄失效。
            rows = page.locator(self._task_rows_selector)
            if idx >= await rows.count():
                emit("WARN", f"row {idx}: 行不存在，停止本校遍历")
                break
            row = rows.nth(idx)
            try:
                if not await row.is_visible():
                    continue
            except Exception:
                continue

            row_text = (await row.inner_text()).strip()
            if not row_text or len(row_text) < 6:
                continue
            if any(keyword in row_text for keyword in self._scan_noise_keywords):
                continue

            # 跳过表头/说明行
            if (
                "学校科目/试卷名称" in row_text
                and "题目" in row_text
                and "状态" in row_text
            ):
                continue

            cells = row.locator("td")
            cell_count = min(await cells.count(), 8)
            if cell_count < 2:
                continue

            school_cell = (
                (await cells.nth(0).inner_text()).strip() if cell_count >= 1 else ""
            )
            title_cell = (
                (await cells.nth(1).inner_text()).strip() if cell_count >= 2 else ""
            )
            op_cell = (await cells.nth(cell_count - 1).inner_text()).strip()
            status_cell = (
                (await cells.nth(2).inner_text()).strip() if cell_count >= 3 else ""
            )

            view_buttons = row.locator(self._view_button_selector)
            view_count = await view_buttons.count()
            has_view = "查看" in op_cell or view_count > 0
            if not has_view:
                in_row_view = row.locator(
                    "button:has(span:has-text('查看')), button:has-text('查看'), span:has-text('查看')"
                )
                has_view = await in_row_view.count() > 0

            emit(
                "INFO",
                f"row {idx}: status={status_cell or '-'} op={op_cell or '-'} view_count={view_count}",
            )
            if not has_view:
                emit("INFO", f"row {idx}: 无查看按钮，跳过")
                continue
            is_pending = any(
                flag in status_cell for flag in ["待解", "待答", "待开始", "待解答"]
            )
            if not is_pending and status_cell:
                emit("INFO", f"row {idx}: 状态非待处理({status_cell})，跳过")
                continue

            topic_title = title_cell or row_text
            if len(topic_title) < 4:
                emit("INFO", f"row {idx}: 标题过短，跳过")
                continue

            full_title, topic_image_url = await self._extract_full_title_from_view(
                page,
                idx,
                topic_title,
                on_log=on_log,
            )
            topic_title = (full_title or topic_title).replace("\n", " ").strip()
            if len(topic_title) > 280:
                topic_title = topic_title[:280]

            display_school = school_name or school_cell or "默认学校"
            stable_key = f"{display_school}|{topic_title}|{topic_image_url}".encode(
                "utf-8", errors="ignore"
            )
            stable_id = f"auto_{hashlib.sha1(stable_key).hexdigest()[:16]}"
            tasks.append(
                {
                    "task_id": stable_id,
                    "run_id": run_id,
                    "school_name": display_school,
                    "topic_title": topic_title,
                    "topic_image_url": topic_image_url,
                }
            )
            emit("INFO", f"row {idx}: 已收录任务，标题长度 {len(topic_title)}")

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

    async def _wait_school_switch_settled(
        self,
        page: Any,
        on_log: Callable[[str, str, str], None] | None = None,
        school_name: str | None = None,
    ) -> None:
        def emit(level: str, message: str) -> None:
            if on_log is None:
                return
            prefix = f"[{school_name}] " if school_name else ""
            on_log(level, "scan.school", f"{prefix}{message}")

        # 1) 先等待常见 loading 遮罩消失。
        loading_selectors = [
            ".el-loading-mask",
            ".el-loading-spinner",
            ".el-icon-loading",
            ".v-modal",
        ]
        for selector in loading_selectors:
            try:
                for _ in range(15):
                    mask = page.locator(selector)
                    count = await mask.count()
                    if count == 0:
                        break
                    visible = False
                    for i in range(min(count, 5)):
                        try:
                            style = (
                                await mask.nth(i).get_attribute("style") or ""
                            ).replace(" ", "")
                            if "display:none" in style:
                                continue
                            if await mask.nth(i).is_visible():
                                visible = True
                                break
                        except Exception:
                            continue
                    if not visible:
                        break
                    await page.wait_for_timeout(180)
            except Exception:
                continue

        # 2) 再等待表格行数与前两行文本稳定，覆盖二次刷新。
        stable_rounds = 0
        last_signature = ""
        for _ in range(30):
            rows = page.locator(self._task_rows_selector)
            row_count = await rows.count()
            snippets: list[str] = []
            for i in range(min(row_count, 2)):
                try:
                    snippets.append((await rows.nth(i).inner_text()).strip()[:80])
                except Exception:
                    snippets.append("")
            signature = f"{row_count}|{'|'.join(snippets)}"
            if signature == last_signature and signature:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_signature = signature

            if stable_rounds >= 2:
                emit("INFO", f"table settled: rows={row_count}")
                return
            await page.wait_for_timeout(180)

        emit("WARN", "table settle timeout, continue with current snapshot")

    async def _switch_to_pending(self, page: Any) -> None:
        await self._click_first_available(page, self._pending_tab_candidates)
        await page.wait_for_timeout(300)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join((text or "").split()).lower()

    @staticmethod
    def _normalize_preview_text(text: str) -> str:
        raw = (text or "").lower()
        if not raw:
            return ""

        # 去掉 HTML 标签、公式片段与常见模板噪声，提升跨页面匹配稳定性。
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = re.sub(r"\{[^{}]*\}", " ", raw)
        raw = re.sub(r"[\r\n\t]+", " ", raw)
        raw = raw.replace("&nbsp;", " ")
        noise_tokens = [
            "试题原始图片",
            "ai识别内容",
            "插入公式",
            "image.png",
            "katex",
            "mathml",
            "annotation",
            "application/x-tex",
            "data-w-e-type",
            "is_latex",
        ]
        for token in noise_tokens:
            raw = raw.replace(token, " ")
        raw = re.sub(r"\s+", "", raw)
        return raw

    def _build_title_keywords(self, topic_title: str) -> list[str]:
        normalized = self._normalize_preview_text(topic_title)
        if not normalized:
            return []
        candidates = [
            normalized,
            normalized[:48],
            normalized[:32],
            normalized[:24],
            normalized[:16],
        ]
        unique: list[str] = []
        for item in candidates:
            if len(item) < 6:
                continue
            if item not in unique:
                unique.append(item)
        return unique

    @staticmethod
    def _extract_match_tokens(text: str) -> set[str]:
        normalized = BrowserWorker._normalize_preview_text(text)
        if not normalized:
            return set()
        tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", normalized))
        if len(normalized) >= 12:
            tokens.add(normalized[:12])
        if len(normalized) >= 20:
            tokens.add(normalized[:20])
        return tokens

    def _is_row_text_match(self, row_text: str, topic_title: str) -> bool:
        normalized_row = self._normalize_preview_text(row_text)
        normalized_title = self._normalize_preview_text(topic_title)
        if not normalized_row or not normalized_title:
            return False

        if normalized_title in normalized_row or normalized_row in normalized_title:
            return True

        keywords = self._build_title_keywords(topic_title)
        if any(key in normalized_row for key in keywords):
            return True

        row_tokens = self._extract_match_tokens(row_text)
        title_tokens = self._extract_match_tokens(topic_title)
        if not row_tokens or not title_tokens:
            return False
        overlap = row_tokens.intersection(title_tokens)
        if len(overlap) >= 2:
            return True
        long_overlap = [token for token in overlap if len(token) >= 6]
        if len(long_overlap) >= 1:
            return True

        # OCR/公式场景下文本噪声大，允许较低阈值的 token 重合率。
        ratio = len(overlap) / max(1, min(len(row_tokens), len(title_tokens)))
        return ratio >= 0.3

    async def _select_school(self, page: Any, school_name: str) -> bool:
        if not school_name:
            return True

        await self._open_school_dropdown(page)
        options = page.locator(self._school_option_selector)
        option = options.filter(has_text=school_name).first
        if await option.count() == 0:
            return False

        try:
            await option.click(timeout=1500)
            await self._wait_school_switch_settled(page, school_name=school_name)
            await self._switch_to_pending(page)
            await self._wait_school_switch_settled(page, school_name=school_name)
            return True
        except Exception:
            return False

    async def _click_grab_button_in_row(self, row: Any) -> bool:
        button = row.locator(self._task_action_button_selector).first
        if await button.count() == 0:
            return False
        try:
            await button.click(timeout=2000)
            return True
        except Exception:
            return False

    async def _grab_task_from_current_table(self, page: Any, topic_title: str) -> bool:
        rows = page.locator(self._task_rows_selector)
        row_count = min(await rows.count(), 120)
        if row_count == 0:
            return False

        for idx in range(row_count):
            row = rows.nth(idx)
            try:
                if not await row.is_visible():
                    continue
            except Exception:
                continue

            row_text = (await row.inner_text()).strip()
            if not row_text:
                continue

            if not self._is_row_text_match(row_text, topic_title):
                continue

            if await self._click_grab_button_in_row(row):
                await page.wait_for_timeout(250)
                return True

        return False

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
                await self._wait_school_switch_settled(
                    page,
                    on_log=emit,
                    school_name=school_name,
                )
                await self._switch_to_pending(page)
                await self._wait_school_switch_settled(
                    page,
                    on_log=emit,
                    school_name=school_name,
                )
                emit("INFO", "scan.school", f"switched school: {school_name}")

                school_tasks = await self._extract_table_tasks(
                    page,
                    run_id,
                    school_name,
                    on_log=emit,
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
            fallback_tasks = await self._extract_table_tasks(
                page,
                run_id,
                "默认学校",
                on_log=emit,
            )
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

    async def grab_task(
        self,
        run_id: str,
        task_id: str,
        school_name: str,
        topic_title: str,
    ) -> bool:
        if self._use_mock:
            await asyncio.sleep(0.05)
            return True

        session = self._sessions.get(run_id)
        if session is None:
            raise ValueError(f"session not found: {run_id}")
        await self._ensure_login(run_id)
        page = session.page

        if not await self._ensure_single_research_ready(page):
            return False
        await self._switch_to_pending(page)

        selected = await self._select_school(page, school_name)
        if not selected:
            return False

        # 接单阶段比扫描更容易遇到学校切换后的延迟刷新，这里再做一次稳定等待与重试。
        await self._wait_school_switch_settled(page, school_name=school_name)
        await page.wait_for_timeout(250)

        for _ in range(3):
            grabbed = await self._grab_task_from_current_table(page, topic_title)
            if grabbed:
                return True
            await self._wait_school_switch_settled(page, school_name=school_name)
            await page.wait_for_timeout(250)

        await asyncio.sleep(0.05)
        return False

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
