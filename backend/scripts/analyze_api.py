#!/usr/bin/env python3
"""
API Traffic Analyzer

分析 capture_api_traffic.py 生成的 .jsonl 文件，
提取 API 端点、请求结构、认证方式，输出可复用的 API 客户端代码。

运行方式:
    python scripts/analyze_api.py [captured_*.jsonl 文件路径]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def analyze_file(jsonl_path: str) -> None:
    path = Path(jsonl_path)
    if not path.exists():
        print(f"ERROR: file not found: {jsonl_path}")
        sys.exit(1)

    requests: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                requests.append(json.loads(line))
            except Exception:
                pass

    if not requests:
        print("ERROR: no valid requests found in file")
        sys.exit(1)

    # 按 URL 聚合
    url_groups: dict[str, list[dict]] = {}
    for r in requests:
        url = r.get("url", "")
        try:
            parsed = url.split("?")[0]
        except Exception:
            parsed = url
        url_groups.setdefault(parsed, []).append(r)

    print("=" * 60)
    print("  API Traffic Analysis Report")
    print("=" * 60)
    print(f"  Total requests: {len(requests)}")
    print(f"  Unique endpoints: {len(url_groups)}")
    print()

    for url, group in sorted(url_groups.items(), key=lambda x: -len(x[1])):
        methods = set(r.get("method", "?") for r in group)
        statuses = set(str(r.get("response_status", "?")) for r in group)
        print(f"  [{', '.join(methods)}] {url}")
        print(f"      calls={len(group)} | statuses={', '.join(statuses)}")

        # 打印第一个 POST 请求的 body
        for r in group:
            if r.get("method") in ("POST", "PUT"):
                pd = r.get("post_data")
                if pd:
                    try:
                        pd_json = json.loads(pd)
                        print(f"      POST body: {json.dumps(pd_json, ensure_ascii=False, indent=4)[:500]}")
                    except Exception:
                        print(f"      POST body: {pd[:300]}")
                break

        # 打印第一个响应 body 摘要
        for r in group:
            rb = r.get("response_body", "")
            if rb:
                try:
                    rb_json = json.loads(rb)
                    print(f"      Response: {json.dumps(rb_json, ensure_ascii=False)[:400]}")
                except Exception:
                    print(f"      Response: {rb[:300]}")
                break
        print()

    # 分析认证头
    print("=" * 60)
    print("  Auth Headers Detected")
    print("=" * 60)
    auth_keys = {"authorization", "token", "x-token", "x-auth-token", "cookie", "set-cookie"}
    for r in requests:
        headers = r.get("headers", {})
        for key, val in headers.items():
            if key.lower() in auth_keys:
                print(f"  {r.get('method')} {r.get('url', '')[:80]}")
                print(f"    {key}: {val[:100]}")
    print()

    # 生成 Python 客户端代码片段
    print("=" * 60)
    print("  Generated API Client Snippet")
    print("=" * 60)
    print("""
# 以下为基于抓包分析的 API 客户端参考实现
# 需要根据实际响应结构调整

import httpx
import asyncio

class XuejieApiClient:
    def __init__(self, base_url: str, headers: dict = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30)
        self.headers = headers or {}

    async def close(self):
        await self.client.aclose()

    async def login(self, username: str, password: str) -> dict:
        resp = await self.client.post(
            f"{self.base_url}/login",  # 需要根据实际端点调整
            json={"username": username, "password": password},
            headers=self.headers,
        )
        resp.raise_for_status()
        # 返回 cookie 或 token
        return dict(resp.cookies)

    async def get_tasks(self, cookies: dict) -> list[dict]:
        resp = await self.client.get(
            f"{self.base_url}/api/tasks",  # 需要根据实际端点调整
            cookies=cookies,
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def grab_task(self, task_id: str, cookies: dict) -> dict:
        resp = await self.client.post(
            f"{self.base_url}/api/grab",  # 需要根据实际端点调整
            json={"task_id": task_id},
            cookies=cookies,
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def submit_answer(self, task_id: str, answer: dict, cookies: dict) -> dict:
        resp = await self.client.post(
            f"{self.base_url}/api/submit",  # 需要根据实际端点调整
            json={"task_id": task_id, **answer},
            cookies=cookies,
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()
""")

    print()
    print("[+] 分析完成。请根据上方的端点摘要和响应结构，")
    print("    确认实际 API 路径后替换生成代码中的占位符。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # 自动查找最新的 jsonl 文件
        backend_dir = Path(__file__).parent.parent
        captured = backend_dir / "captured_traffic"
        if captured.exists():
            files = sorted(captured.glob("captured_*.jsonl"), key=lambda p: -p.stat().st_mtime)
            if files:
                jsonl_path = str(files[0])
                print(f"[*] 自动使用最新文件: {jsonl_path}")
            else:
                print("ERROR: 未找到 captured_traffic/*.jsonl 文件，请先运行 capture_api_traffic.py")
                sys.exit(1)
        else:
            print("ERROR: 未找到 captured_traffic/ 目录，请先运行 capture_api_traffic.py")
            sys.exit(1)
    else:
        jsonl_path = sys.argv[1]

    analyze_file(jsonl_path)
