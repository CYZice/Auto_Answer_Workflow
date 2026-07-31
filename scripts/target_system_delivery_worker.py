"""在同一持久 Chromium 会话中完成目标系统的半自动交付。

答案正文只能通过目标系统的“识别录入”写入；本脚本只额外填写考点延伸。
最终提交和放弃作答始终由人工完成，且在网络层被禁止。
"""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from playwright.async_api import BrowserContext, Page, async_playwright


ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT / "backend", ROOT):
    if import_root.is_dir() and str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))
API_BASE = os.getenv("TARGET_SYSTEM_WORKER_API", "http://localhost:35828").rstrip("/")
RENDER_BASE = os.getenv("TARGET_SYSTEM_RENDER_BASE", API_BASE).rstrip("/")
CHROME = os.getenv("TARGET_SYSTEM_CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PROFILE = Path(os.getenv("TARGET_SYSTEM_BROWSER_PROFILE", ROOT / "data" / "target-system-browser-profile"))
CALIBRATION_DIR = Path(os.getenv("TARGET_SYSTEM_CALIBRATION_DIR", ROOT / "data" / "target-system-calibration" / "current"))
HEADLESS = os.getenv("TARGET_SYSTEM_WORKER_HEADLESS", "1").lower() not in {"0", "false", "no"}
TERMINAL_TOKENS = ("submit", "abandon", "giveup")
SPA_PATH = "#/xueba/ai_research"


def read_config() -> dict[str, str]:
    from app.services.target_system_client import read_target_config

    return read_target_config()


def load_contract() -> dict[str, object]:
    path = CALIBRATION_DIR / "browser-contract.json"
    if not path.is_file():
        raise RuntimeError(f"未找到浏览器校准产物：{path}。请先运行 capture_target_system_calibration.py。")
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"浏览器校准产物不是有效 JSON：{path}") from exc
    required = ("open_button_selector", "recognize_button_text", "upload_selector", "ocr_editor_selector", "exam_point_selector")
    missing = [key for key in required if not isinstance(contract.get(key), str) or not str(contract[key]).strip()]
    if missing:
        raise RuntimeError(f"浏览器校准产物缺少字段：{', '.join(missing)}")
    return contract


async def render_answer_png(page: Page, item: dict) -> Path:
    output = Path(tempfile.mkdtemp(prefix="target-answer-")) / "answer.png"
    task_id = str(item.get("workflow_task_id") or "")
    if not task_id:
        raise RuntimeError("交付任务缺少关联的解题任务。")
    await page.goto(
        f"{RENDER_BASE}/?target-render-task={quote(task_id, safe='')}&target-delivery-answer=1",
        wait_until="networkidle",
    )
    preview = page.get_by_test_id("target-answer-render")
    await preview.wait_for(timeout=30_000)
    await page.evaluate("document.fonts && document.fonts.ready")
    await preview.screenshot(path=str(output))
    return output


async def protect_terminal_actions(context: BrowserContext) -> None:
    async def on_route(route):
        request = route.request
        path = urlparse(request.url).path.lower()
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and any(token in path for token in TERMINAL_TOKENS):
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", on_route)
    await context.add_init_script(
        """() => {
          const terminal = /确认提交答案|确认提交|放弃作答/;
          const protect = () => document.querySelectorAll('button,a,[role=button]').forEach((element) => {
            if (terminal.test(element.innerText || '')) {
              element.setAttribute('aria-disabled', 'true');
              element.style.pointerEvents = 'none';
              element.style.opacity = '0.45';
            }
          });
          document.addEventListener('click', (event) => {
            const target = event.target.closest('button,a,[role=button]');
            if (target && terminal.test(target.innerText || '')) {
              event.preventDefault(); event.stopImmediatePropagation();
            }
          }, true);
          new MutationObserver(protect).observe(document.documentElement, {childList: true, subtree: true});
          protect();
        }"""
    )


async def login_if_needed(page: Page, config: dict[str, str]) -> None:
    await page.goto(f"{config['base_url'].rstrip('/')}/{SPA_PATH}", wait_until="domcontentloaded")
    try:
        await page.wait_for_function(
            """() => document.querySelectorAll('input[placeholder*="账号"], input[placeholder*="密码"]').length >= 2
              || [...document.querySelectorAll('body *')].some((node) => (node.textContent || '').trim() === '识别录入')""",
            timeout=30_000,
        )
    except Exception as exc:
        raise RuntimeError("目标系统作答页加载超时，未出现登录或识别录入控件。") from exc
    if await page.get_by_text("识别录入", exact=True).count():
        return
    inputs = page.locator('input[placeholder*="账号"], input[placeholder*="密码"]')
    if await inputs.count() < 2:
        raise RuntimeError("目标系统未进入作答页，且未找到登录表单。")
    await inputs.nth(0).fill(config["username"])
    await inputs.nth(1).fill(config["password"])
    async with page.expect_response(
        lambda response: response.request.method == "POST" and "/admin/login" in response.url,
        timeout=30_000,
    ) as login_response:
        await page.get_by_role("button", name="立即登录", exact=True).click()
    payload = await (await login_response.value).json()
    if not isinstance(payload, dict) or payload.get("code") not in (0, 200):
        raise RuntimeError(str(payload.get("message") or payload.get("msg") or "目标系统登录失败"))
    await page.goto(f"{config['base_url'].rstrip('/')}/{SPA_PATH}", wait_until="networkidle")
    if await page.locator('input[placeholder*="账号"], input[placeholder*="密码"]').count() >= 2:
        raise RuntimeError("目标系统登录后仍停留在登录页。")


async def open_ai_research_shortcut() -> None:
    """打开一个可见、已登录的 AI Research 页面，供人工继续录入。"""
    config = read_config()
    PROFILE.mkdir(parents=True, exist_ok=True)
    executable = CHROME if Path(CHROME).exists() else None
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(PROFILE), executable_path=executable, headless=False, viewport={"width": 1440, "height": 960}
        )
        await protect_terminal_actions(context)
        page = await context.new_page()
        await login_if_needed(page, config)
        print("已打开并登录学解 AI Research 页面。关闭浏览器窗口后，快捷打开进程会自动结束。")
        try:
            await page.wait_for_event("close")
        finally:
            await context.close()


async def open_remote_topic(page: Page, remote_task_id: str, contract: dict[str, object], list_rows: list[dict]) -> None:
    open_selector = str(contract["open_button_selector"])
    row_template = str(contract.get("topic_row_selector") or "")
    active_tab = page.get_by_text(str(contract.get("active_tab_text") or "解题中"), exact=True)
    if await active_tab.count():
        await active_tab.first.click()
        await page.wait_for_timeout(1_000)
    row = None
    if row_template:
        selector = row_template.replace("{remote_task_id}", remote_task_id)
        candidate = page.locator(selector)
        if await candidate.count():
            row = candidate.first
    if row is None:
        id_text = page.get_by_text(remote_task_id, exact=True)
        if await id_text.count():
            row = id_text.first.locator("xpath=ancestor::*[self::tr or @role='row' or contains(@class, 'row')][1]")
    if row is None:
        row_index = next(
            (
                index
                for index, item in enumerate(list_rows)
                if str(item.get("id") or item.get("topic_id") or item.get("ai_topic_id") or "") == remote_task_id
            ),
            None,
        )
        buttons = page.locator(open_selector)
        if row_index is None or await buttons.count() <= row_index:
            raise RuntimeError(f"页面中未找到远端题目 {remote_task_id}；请重新校准列表定位规则。")
        await buttons.nth(row_index).click()
    else:
        button = row.locator(open_selector)
        if not await button.count():
            raise RuntimeError(f"题目 {remote_task_id} 未找到查看入口；请重新校准列表定位规则。")
        await button.first.click()
    await page.get_by_text(str(contract["recognize_button_text"]), exact=True).wait_for(timeout=20_000)


async def fill_exam_point(page: Page, selector: str, value: str) -> None:
    success = await page.evaluate(
        """({ selector, value }) => {
          const field = document.querySelector(selector);
          if (!field) return false;
          field.focus();
          if (field.isContentEditable) {
            field.textContent = value;
          } else {
            field.value = value;
          }
          field.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
          field.dispatchEvent(new Event('change', {bubbles: true}));
          field.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
          return (field.isContentEditable ? field.innerText : field.value).trim() === value.trim();
        }""",
        {"selector": selector, "value": value},
    )
    if not success:
        raise RuntimeError("未找到或无法写入考点延伸输入框；请重新校准页面控件。")


async def process_item(context: BrowserContext, item: dict) -> tuple[Path, Path]:
    contract = load_contract()
    config = read_config()
    page = context.pages[0] if context.pages else await context.new_page()
    list_rows: list[dict] = []

    async def capture_list(response) -> None:
        if "/admin/research/aiTopicList" not in response.url:
            return
        try:
            payload = await response.json()
        except Exception:
            return
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("list") if isinstance(data, dict) else None
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            list_rows[:] = rows

    page.on("response", capture_list)
    image_page = await context.new_page()
    answer_image = await render_answer_png(image_page, item)
    await image_page.close()

    exam_point = str(item.get("exam_point") or "").strip()
    if not exam_point:
        raise RuntimeError("交付任务缺少【考点延伸】内容。")
    await login_if_needed(page, config)
    await open_remote_topic(page, str(item["remote_task_id"]), contract, list_rows)

    recognize = page.get_by_text(str(contract["recognize_button_text"]), exact=True)
    await recognize.click()
    upload = page.locator(str(contract["upload_selector"])).last
    await upload.wait_for(timeout=10_000)
    ocr_editor = page.locator(str(contract["ocr_editor_selector"])).first
    async with page.expect_response(
        lambda response: response.request.method == "POST" and "/admin/openai/imgToText" in response.url and response.status == 200,
        timeout=60_000,
    ):
        await upload.set_input_files(str(answer_image))
    await ocr_editor.wait_for(timeout=30_000)
    await page.wait_for_function(
        "element => (element.innerText || element.textContent || '').trim().length > 0",
        await ocr_editor.element_handle(),
        timeout=60_000,
    )
    await fill_exam_point(page, str(contract["exam_point_selector"]), exam_point)
    screenshot = answer_image.with_name("filled-page.png")
    await page.screenshot(path=str(screenshot), full_page=True)
    return answer_image, screenshot


async def report(client: httpx.AsyncClient, item_id: int, state: str, screenshot: Path | None, answer_image: Path | None = None, message: str = "") -> None:
    files = {}
    if screenshot and screenshot.exists():
        files["screenshot"] = (screenshot.name, screenshot.read_bytes(), "image/png")
    if answer_image and answer_image.exists():
        files["rendered_answer"] = (answer_image.name, answer_image.read_bytes(), "image/png")
    if state == "filled":
        response = await client.post(f"{API_BASE}/api/target-system/delivery/{item_id}/filled", files=files or None)
    else:
        response = await client.post(f"{API_BASE}/api/target-system/delivery/{item_id}/failed", data={"error_message": message[:2000]}, files=files)
    response.raise_for_status()


async def main() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    executable = CHROME if Path(CHROME).exists() else None
    async with httpx.AsyncClient(timeout=30) as client, async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(PROFILE), executable_path=executable, headless=HEADLESS, viewport={"width": 1440, "height": 960}
        )
        await protect_terminal_actions(context)
        print("目标系统交付工作器已启动：在同一浏览器会话中填写答案和考点，绝不最终提交。")
        try:
            while True:
                try:
                    payload = (await client.get(f"{API_BASE}/api/target-system/delivery/current")).json()
                    item = payload.get("item")
                    if not item or item.get("status") != "filling":
                        await asyncio.sleep(2)
                        continue
                    try:
                        if item.get("delivery_content_error"):
                            raise RuntimeError(str(item["delivery_content_error"]))
                        answer_image, screenshot = await process_item(context, item)
                        await report(client, int(item["id"]), "filled", screenshot, answer_image)
                        print(f"题目 {item['remote_task_id']} 已填入当前浏览器，等待人工提交。")
                    except Exception as exc:
                        page = context.pages[0] if context.pages else None
                        screenshot = None
                        if page:
                            screenshot = Path(tempfile.mkdtemp(prefix="target-failure-")) / "failure.png"
                            await page.screenshot(path=str(screenshot), full_page=True)
                        await report(client, int(item["id"]), "failed", screenshot, message=str(exc))
                        print(f"题目 {item['remote_task_id']} 浏览器填入失败：{exc}", file=sys.stderr)
                except Exception as exc:
                    print(f"工作器轮询失败：{exc}", file=sys.stderr)
                    await asyncio.sleep(3)
        finally:
            await context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="目标系统浏览器交付工具")
    parser.add_argument("--open-ai-research", action="store_true", help="仅打开并自动登录 AI Research 页面")
    args = parser.parse_args()
    asyncio.run(open_ai_research_shortcut() if args.open_ai_research else main())
