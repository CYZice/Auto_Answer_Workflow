#!/usr/bin/env python3
"""
Full Traffic Capture - 捕获所有请求，不限定路径
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    from playwright.async_api import async_playwright, Request
except ImportError:
    print("ERROR: playwright not installed.")
    sys.exit(1)


TARGET_URL = "https://yy.xuejie.cn/#/login"
OUTPUT_DIR = Path(__file__).parent.parent / "captured_traffic"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class TrafficCapture:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.requests: list[dict] = []
        self.start_time = time.time()
        self.output_file = OUTPUT_DIR / f"full_capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _record_request(self, request: Request) -> None:
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
                post_preview = f" | {json.dumps(post_json, ensure_ascii=False)[:200]}"
            except Exception:
                post_preview = f" | {str(post_data)[:200]}"
        print(f"  [{request.method}] {request.url}{post_preview}")

    def _on_response(self, response) -> None:
        url = response.url
        for record in reversed(self.requests):
            if record["url"] == url:
                record["response_status"] = response.status
                # response.text() 是协程，无法同步获取，直接跳过 response_body
                break

    async def run(self):
        print("=" * 60)
        print("  Full Traffic Capture (ALL requests)")
        print("=" * 60)
        print(f"  Target: {TARGET_URL}")
        print(f"  Output: {self.output_file}")
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
        print("[*] LOGIN SUCCESS! Please perform these actions:")
        print("    1. Go to single-question research -> pending")
        print("    2. Open school dropdown")
        print("    3. Grab a task")
        print("    4. Fill answer and submit")
        print()

        wait_seconds = int(os.environ.get("AUTOMATION_CAPTURE_WAIT", "90"))
        print(f"[*] Auto-finish in {wait_seconds}s...")

        for remaining in range(wait_seconds, 0, -30):
            await asyncio.sleep(min(30, remaining))
            print(f"[*] Remaining: {remaining}s | captured: {len(self.requests)} requests")

        await self._save_and_close()

    async def _save_and_close(self) -> None:
        print(f"\n[*] Captured {len(self.requests)} requests")
        with open(self.output_file, "w", encoding="utf-8") as f:
            for record in self.requests:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(f"[+] Saved to: {self.output_file}")

        # 按资源类型统计
        types: dict = {}
        for r in self.requests:
            rt = r.get("resource_type", "?")
            types[rt] = types.get(rt, 0) + 1
        print("\n[*] Resource types:")
        for t, c in sorted(types.items(), key=lambda x: -x[1]):
            print(f"    {c:3d}x {t}")

        # 按域名统计
        domains: dict = {}
        for r in self.requests:
            try:
                domain = urlparse(r.get("url", "")).netloc
                domains[domain] = domains.get(domain, 0) + 1
            except Exception:
                pass
        print("\n[*] Domains:")
        for d, c in sorted(domains.items(), key=lambda x: -x[1]):
            print(f"    {c:3d}x {d}")

        # 只打印 POST 到 yy.xuejie.cn 的 API 调用（核心业务）
        print("\n[*] POST API calls to yy.xuejie.cn:")
        for r in self.requests:
            if r.get("method") == "POST" and "yy.xuejie.cn" in r.get("url", ""):
                url = r.get("url", "")
                body = r.get("post_data", "")
                print(f"    {url}")
                if body:
                    try:
                        body_json = json.loads(body)
                        print(f"      {json.dumps(body_json, ensure_ascii=False)[:300]}")
                    except Exception:
                        print(f"      {body[:200]}")

        await self.browser.close()
        await self.playwright.stop()


async def main():
    username = os.environ.get("AUTOMATION_CAPTURE_USER", "")
    password = os.environ.get("AUTOMATION_CAPTURE_PASS", "")

    if len(sys.argv) >= 3:
        username = sys.argv[1]
        password = sys.argv[2]
    elif not username or not password:
        print("Usage: python scripts/capture_all_traffic.py <username> <password>")
        sys.exit(1)

    capture = TrafficCapture(username, password)
    try:
        await capture.run()
    except asyncio.CancelledError:
        await capture._save_and_close()


if __name__ == "__main__":
    asyncio.run(main())
