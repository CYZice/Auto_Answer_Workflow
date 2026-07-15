"""本地可见 Chrome 校准：记录 SPA 控件，严格阻止最终提交和放弃。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
BASE_URL = os.getenv("TARGET_SYSTEM_BASE_URL", "https://yy.xuejie.cn").rstrip("/")
CHROME_EXECUTABLE = os.getenv(
    "TARGET_SYSTEM_CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)
OUTPUT = Path(os.getenv("TARGET_SYSTEM_CALIBRATION_OUTPUT", ROOT / "data" / "target-system-calibration" / "current"))
PROFILE = ROOT / "data" / "target-system-calibration-browser-profile"
TOPIC_ID = os.getenv("TARGET_SYSTEM_CALIBRATION_TOPIC_ID", "89611").strip()
TERMINAL_PATH_TOKENS = ("submit", "abandon", "giveup")
TERMINAL_LABEL = re.compile(r"确认提交|放弃作答")


def redact(value, key: str = ""):
    lowered = key.lower()
    if any(token in lowered for token in ("password", "token", "authorization", "cookie")):
        return "***"
    if isinstance(value, dict):
        return {str(name): redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, str) and (value.startswith("data:") or len(value) > 20_000):
        return "<binary-or-large-text>"
    return value


def request_payload(request):
    try:
        return redact(request.post_data_json)
    except Exception:
        return redact(request.post_data or "")


async def protect_terminal_buttons(page) -> None:
    await page.evaluate(
        """() => {
          const protect = () => document.querySelectorAll('button').forEach((button) => {
            if (/确认提交|放弃作答/.test(button.innerText || '')) {
              button.disabled = true;
              button.style.pointerEvents = 'none';
              button.style.opacity = '0.45';
            }
          });
          document.addEventListener('click', (event) => {
            const button = event.target.closest('button');
            if (button && /确认提交|放弃作答/.test(button.innerText || '')) {
              event.preventDefault(); event.stopImmediatePropagation();
            }
          }, true);
          new MutationObserver(protect).observe(document.documentElement, {childList: true, subtree: true});
          protect();
        }"""
    )


async def main() -> None:
    from playwright.async_api import async_playwright

    OUTPUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    done_file = OUTPUT / "finish"
    done_file.unlink(missing_ok=True)
    blocked: list[dict] = []
    observed_posts: list[dict] = []
    executable = CHROME_EXECUTABLE if Path(CHROME_EXECUTABLE).exists() else None

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(PROFILE), executable_path=executable, headless=False, viewport={"width": 1440, "height": 960}
        )
        page = context.pages[0] if context.pages else await context.new_page()

        def on_request(request) -> None:
            path = urlparse(request.url).path
            if request.method == "POST":
                observed_posts.append({"url": f"{urlparse(request.url).scheme}://{urlparse(request.url).netloc}{path}", "payload": request_payload(request)})

        async def block_terminal_route(route) -> None:
            request = route.request
            blocked.append({
                "method": request.method,
                "url": f"{urlparse(request.url).scheme}://{urlparse(request.url).netloc}{urlparse(request.url).path}",
                "payload": request_payload(request),
            })
            await route.abort()

        async def on_response(response):
            path = urlparse(response.url).path
            if path not in {"/admin/research/aiTopicInfo", "/admin/research/aiTopicList", "/admin/openai/imgToText"}:
                return
            try:
                payload = await response.json()
            except Exception:
                return
            observed_posts.append({"response": f"{response.status} {path}", "payload": redact(payload)})

        for token in TERMINAL_PATH_TOKENS:
            await context.route(f"**/*{token}*", block_terminal_route)
        context.on("request", on_request)
        page.on("response", on_response)
        await page.goto(f"{BASE_URL}/#/xueba/ai_research", wait_until="domcontentloaded")
        await protect_terminal_buttons(page)
        print("Chrome 已打开。请手动定位题目 89611，完成一次识别录入，并点击考点延伸输入框。")
        print("确认提交和放弃作答会被阻止；完成后保持题目弹窗打开，再创建 finish 文件。")
        print(f"完成后创建信号文件：{done_file}")
        while not done_file.exists():
            await asyncio.sleep(1)

        await page.screenshot(path=str(OUTPUT / "final-page.png"), full_page=True)
        selectors = await page.evaluate("""() => Array.from(document.querySelectorAll('button,input,textarea,[contenteditable=true]')).map(el => ({tag: el.tagName, type: el.getAttribute('type'), text: (el.innerText || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '').trim().slice(0, 80), name: el.getAttribute('name'), id: el.id, className: String(el.className || '').slice(0, 120)})).filter(x => x.text || x.name || x.id)""")
        contract = await page.evaluate("""(topicId) => {
          const cssEscape = (value) => CSS.escape(String(value));
          const selectorFor = (element) => {
            if (!element) return '';
            if (element.id) return `#${cssEscape(element.id)}`;
            if (element.getAttribute('name')) return `${element.tagName.toLowerCase()}[name="${cssEscape(element.getAttribute('name'))}"]`;
            if (element.getAttribute('data-testid')) return `[data-testid="${cssEscape(element.getAttribute('data-testid'))}"]`;
            if (element.getAttribute('data-topic-id')) return `[data-topic-id="${cssEscape(element.getAttribute('data-topic-id'))}"]`;
            if (element.getAttribute('data-id')) return `[data-id="${cssEscape(element.getAttribute('data-id'))}"]`;
            if (element.getAttribute('placeholder')) return `${element.tagName.toLowerCase()}[placeholder="${cssEscape(element.getAttribute('placeholder'))}"]`;
            return '';
          };
          const fields = [...document.querySelectorAll('input,textarea,[contenteditable=true]')];
          const exam = fields.find((field) => (field.closest('div,form,section,li')?.innerText || '').includes('考点延伸'));
          const ocrEditors = [...document.querySelectorAll('[contenteditable=true]')];
          const focused = document.activeElement;
          const topicText = [...document.querySelectorAll('td,div,li,tr')].find((element) => (element.innerText || '').trim() === topicId);
          const topicRow = topicText?.closest('tr,[role=row],.row,li,div');
          return {
            version: 1,
            open_button_selector: "button:has-text('查看')",
            recognize_button_text: '识别录入',
            upload_selector: 'input[type=file]',
            ocr_editor_selector: selectorFor(ocrEditors[0]) || '[contenteditable=true]',
            exam_point_selector: selectorFor(exam) || selectorFor(focused) || '',
            focused_selector: selectorFor(focused),
            topic_row_selector: selectorFor(topicRow),
          };
        }""", TOPIC_ID)
        (OUTPUT / "selectors.json").write_text(json.dumps(selectors, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUTPUT / "blocked-requests.json").write_text(json.dumps(blocked, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUTPUT / "observed-posts.json").write_text(json.dumps(observed_posts, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUTPUT / "browser-contract.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        await context.close()

    if not contract.get("exam_point_selector"):
        print("校准未识别考点延伸输入框。请在该输入框获得焦点后重新校准。")
    else:
        print(f"校准完成：{OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
