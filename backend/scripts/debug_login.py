#!/usr/bin/env python3
import asyncio
import httpx

async def main():
    resp = await httpx.AsyncClient(timeout=30).post(
        "https://yy.xuejie.cn/admin/login",
        json={"username": "13320115908", "password": "2011590xue"},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    print("Status:", resp.status_code)
    print("Body:", resp.text[:2000])

asyncio.run(main())
