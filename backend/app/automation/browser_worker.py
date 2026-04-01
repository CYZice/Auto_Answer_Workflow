from __future__ import annotations

import asyncio
import uuid


class BrowserWorker:
    def __init__(self):
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def close(self) -> None:
        self._running = False

    async def scan_discovered_tasks(self, run_id: str) -> list[dict]:
        await asyncio.sleep(0.1)
        # MVP: 提供可联调的模拟数据，后续可替换为真实 Playwright 抓取。
        return [
            {
                "task_id": f"auto_{uuid.uuid4().hex[:10]}",
                "run_id": run_id,
                "school_name": "默认学校",
                "topic_title": "示例待解题",
                "topic_image_url": "",
            }
        ]

    async def grab_task(self, task_id: str) -> bool:
        await asyncio.sleep(0.05)
        return True

    async def write_solution(
        self, task_id: str, image_path: str | None, extension_text: str
    ) -> bool:
        await asyncio.sleep(0.05)
        return True

    async def submit_task(self, task_id: str) -> bool:
        await asyncio.sleep(0.05)
        return True
