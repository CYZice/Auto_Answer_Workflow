#!/usr/bin/env python3
"""
API Traffic Capture Script

使用 Playwright 拦截目标站点的所有 HTTP 请求，
记录登录、扫描、抢单、提交等操作对应的真实 API 调用。

运行方式:
    python scripts/capture_api_traffic.py <username> <password>
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright, Request
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright")
    sys.exit(1)


# ── 配置 ──────────────────────────────────────────────────────────
TARGET_URL = "https://yy.xuejie.cn/#/login"
INTERCEPT_PATTERNS = [
    "**/api/**",
    "**/*.php",
]
EXCLUDE_PATTERNS = [
    "**/static/**",
    "**/assets/**",
    "**/*.css",
    "**/*.js",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.svg",
    "**/fonts/**",
    "**/favicon",
]

OUTPUT_DIR = Path(__file__).parent.parent / "captured_traffic"
# ─────────────────────────────────────────────────────────────────


class TrafficCapture:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.requests: list[dict] = []
        self.start_time = time.time()
        self.output_file = OUTPUT_DIR / f"captured_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _should_capture(self, url: str) -> bool:
        for pattern in EXCLUDE_PATTERNS:
            if fnmatch.fnmatch(url.lower(), pattern.lower()):
                return False
        for pattern in INTERCEPT_PATTERNS:
            if fnmatch.fnmatch(url.lower(), pattern.lower()):
                return True
        return False

    def _record_request(self, request: Request) -> None:
        if not self._should_capture(request.url):
            return

        post_data = None
        if request.method in ("POST", "PUT"):
            try:
                post_data = request.post_data
            except Exception:
                pass

        record = {
            "timestamp": round(time.time() - self.start_time, 3),
            "method": request.method,
            "url": request.url,
            "resource_type": request.resource_type,
            "headers": dict(request.headers),
            "post_data": post_data,
        }
        self.requests.append(record)

        post_preview = ""
        if post_data:
            try:
                post_json = json.loads(post_data)
                post_preview = f" | {json.dumps(post_json, ensure_ascii=False)[:300]}"
            except Exception:
                post_preview = f" | {str(post_data)[:300]}"
        print(f"  [{request.method}] {request.url}{post_preview}")

    def _on_response(self, response) -> None:
        url = response.url
        for record in reversed(self.requests):
            if record["url"] == url:
                record["response_status"] = response.status
                try:
                    record["response_body"] = response.text()
                except Exception:
                    pass
                try:
                    record["response_headers"] = dict(response.headers)
                except Exception:
                    pass
                break

    async def run(self):
        print("=" * 60)
        print("  API Traffic Capture Tool")
        print("=" * 60)
        print(f"  Target: {TARGET_URL}")
        print(f"  Output: {self.output_file}")
        print(f"  Account: {self.username}")
        print("=" * 60)
        print()

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            executable_path="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1400, "height": 900},
        )
        self.page = await self.context.new_page()

        self.page.on("request", lambda r: self._record_request(r))
        self.page.on("response", lambda res: self._on_response(res))

        print("[*] Opening login page...")
        await self.page.goto(TARGET_URL, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2000)

        print("[*] Filling login info...")
        await self.page.fill("input[type='text'][placeholder='请输入账号']", self.username)
        await self.page.fill("input[type='password'][placeholder='请输入密码']", self.password)
        await self.page.click("button.el-button.el-button--primary.el-button--default")
        await self.page.wait_for_timeout(3000)

        print()
        print("[*] LOGIN SUCCESS! Please perform these actions in the browser:")
        print("    1. Go to single-question research -> pending tasks")
        print("    2. Open school dropdown to browse task list")
        print("    3. Find a task, click View -> I'll do it (grab)")
        print("    4. Fill test content and click Submit")
        print()

        wait_seconds = int(os.environ.get("AUTOMATION_CAPTURE_WAIT", "300"))
        print(f"[*] Script will auto-finish in {wait_seconds} seconds")
        print(f"[*] (Set AUTOMATION_CAPTURE_WAIT env to adjust timeout)")
        print()

        # Wait with periodic logging
        for remaining in range(wait_seconds, 0, -30):
            await asyncio.sleep(min(30, remaining))
            print(f"[*] Time remaining: {remaining}s | captured: {len(self.requests)} requests")

        await self._save_and_close()

    async def _save_and_close(self) -> None:
        print(f"\n[*] Captured {len(self.requests)} requests")
        with open(self.output_file, "w", encoding="utf-8") as f:
            for record in self.requests:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(f"[+] Saved to: {self.output_file}")

        api_endpoints: dict[str, int] = {}
        for r in self.requests:
            url = r.get("url", "")
            try:
                parsed = url.split("?")[0]
                api_endpoints[parsed] = api_endpoints.get(parsed, 0) + 1
            except Exception:
                pass

        print("\n[*] API Endpoint Summary:")
        for endpoint, count in sorted(api_endpoints.items(), key=lambda x: -x[1]):
            print(f"    {count:3d}x {endpoint}")

        await self.browser.close()
        await self.playwright.stop()


async def main():
    username = os.environ.get("AUTOMATION_CAPTURE_USER", "")
    password = os.environ.get("AUTOMATION_CAPTURE_PASS", "")

    if len(sys.argv) >= 3:
        username = sys.argv[1]
        password = sys.argv[2]
    elif not username or not password:
        print("Usage: python scripts/capture_api_traffic.py <username> <password>")
        print("  Or set env vars: AUTOMATION_CAPTURE_USER / AUTOMATION_CAPTURE_PASS")
        sys.exit(1)

    capture = TrafficCapture(username, password)
    try:
        await capture.run()
    except asyncio.CancelledError:
        await capture._save_and_close()


if __name__ == "__main__":
    asyncio.run(main())
