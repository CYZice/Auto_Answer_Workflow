"""Only-read target-system client. Saving/submitting is deliberately absent."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any
import httpx
import yaml


_shared_sessions: dict[tuple[str, str], dict[str, Any]] = {}
_shared_session_locks: dict[tuple[str, str], asyncio.Lock] = {}


def is_target_auth_failure(status_code: int, body: Any) -> bool:
    if status_code in (401, 403):
        return True
    if not isinstance(body, dict):
        return False
    code = body.get("code")
    message = str(body.get("message") or body.get("msg") or "").lower()
    return code in (401, 403, 505) or any(token in message for token in ("请先登录", "登录失效", "token", "未登录"))


def read_target_config() -> dict[str, str]:
    path = Path(os.getenv("CONFIG_DIR", Path(__file__).resolve().parents[3] / "config")) / "target_system.local.private.yaml"
    if not path.exists():
        raise RuntimeError("未配置 config/target_system.local.private.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = {str(key): str(value or "").strip() for key, value in raw.items()}
    return {key: config.get(key, "") for key in ("base_url", "username", "password")}


class TargetSystemClient:
    def __init__(self, config: dict[str, str] | None = None):
        self.config = config or read_target_config()
        self.base_url = self.config["base_url"].rstrip("/")
        self.token = ""
        self.cookies: dict[str, str] = {}
        self.developer_id = 0

    def _session_key(self) -> tuple[str, str]:
        return self.base_url, self.config["username"]

    def _apply_session(self, session: dict[str, Any]) -> None:
        self.token = str(session["token"])
        self.cookies = dict(session["cookies"])
        self.developer_id = int(session["developer_id"])

    async def login(self, force: bool = False) -> None:
        key = self._session_key()
        if not force and key in _shared_sessions:
            self._apply_session(_shared_sessions[key])
            return
        lock = _shared_session_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not force and key in _shared_sessions:
                self._apply_session(_shared_sessions[key])
                return
            await self._login_remote()
            _shared_sessions[key] = {
                "token": self.token,
                "cookies": dict(self.cookies),
                "developer_id": self.developer_id,
            }

    async def _login_remote(self) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/admin/login", json={"username": self.config["username"], "password": self.config["password"]})
        payload = response.json()
        data = payload.get("data") or {}
        if response.status_code != 200 or payload.get("code") not in (0, 200) or not data.get("token"):
            raise RuntimeError("目标题目系统登录失败")
        self.token = str(data["token"])
        self.cookies = {"token": self.token}
        if data.get("uuid"):
            self.cookies["uuid"] = str(data["uuid"])
        self.developer_id = int((data.get("info") or {}).get("developer_id") or 0)

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        if not self.token:
            await self.login()
        for attempt in range(2):
            headers = {"Authorization": self.token, "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.base_url}{path}", json=payload, headers=headers, cookies=self.cookies)
            try:
                body = response.json()
            except ValueError as exc:
                raise RuntimeError("远端返回了无法识别的响应。") from exc
            if response.status_code == 200 and body.get("code") in (0, 200):
                return body.get("data") or {}
            if attempt == 0 and is_target_auth_failure(response.status_code, body):
                await self.login(force=True)
                continue
            raise RuntimeError(str(body.get("message") or body.get("msg") or "远端请求失败"))
        raise RuntimeError("远端请求失败")

    async def list_schools(self) -> list[dict[str, Any]]:
        data = await self.post("/admin/school/list", {"is_business": 1, "schoolIds": []})
        return data.get("list", []) if isinstance(data, dict) else []

    async def list_tasks(self, school_id: int = 0, subject_ids: list[int] | None = None, page: int = 1, pagesize: int = 50) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "school_id": school_id,
            "status": 0,
            "judge_admin_id": "",
            "developer_id": self.developer_id,
            "page": page,
            "pagesize": pagesize,
        }
        if subject_ids:
            payload["subject_ids"] = subject_ids
            payload["selfCate"] = subject_ids[0]
        data = await self.post("/admin/research/aiTopicList", payload)
        return data.get("list", []) if isinstance(data, dict) else []

    async def list_all_pending_tasks(self, pagesize: int = 50) -> list[dict[str, Any]]:
        """兼容调用方：汇总所有 API 分页结果。"""
        collected: dict[str, dict[str, Any]] = {}
        async for batch in self.iter_pending_task_batches(pagesize=pagesize):
            for row in batch["rows"]:
                if row.get("id") is not None:
                    collected[str(row["id"])] = row
        return list(collected.values())

    async def iter_pending_task_batches(self, pagesize: int = 50, school_id: int | None = None, subject_ids: list[int] | None = None) -> AsyncIterator[dict[str, Any]]:
        """按学校和分页返回题目，使调用方可边拉取边落库、边展示。"""
        if school_id is not None:
            school_entries = [(school_id, f"学校{school_id}")]
        else:
            schools = await self.list_schools()
            school_entries = [(0, "未指定学校")]
            seen_school_ids = {0}
            for school in schools:
                try:
                    listed_school_id = int(school.get("school_id") or 0)
                except (TypeError, ValueError):
                    continue
                if listed_school_id and listed_school_id not in seen_school_ids:
                    school_name = str(school.get("school_name") or school.get("name") or f"学校{listed_school_id}").strip()
                    school_entries.append((listed_school_id, school_name))
                    seen_school_ids.add(listed_school_id)
        for school_index, (school_id, school_name) in enumerate(school_entries, start=1):
            page = 1
            while True:
                rows = await self.list_tasks(school_id=school_id, subject_ids=subject_ids, page=page, pagesize=pagesize)
                normalized_rows: list[dict[str, Any]] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    normalized = dict(row)
                    paper = normalized.get("paperInfo")
                    paper_info = dict(paper) if isinstance(paper, dict) else {}
                    paper_info.setdefault("school_id", school_id)
                    paper_info.setdefault("school_name", school_name)
                    normalized["paperInfo"] = paper_info
                    normalized_rows.append(normalized)
                yield {
                    "school_index": school_index,
                    "school_total": len(school_entries),
                    "school_id": school_id,
                    "school_name": school_name,
                    "page": page,
                    "rows": normalized_rows,
                }
                if len(rows) < pagesize:
                    break
                page += 1

    async def detail(self, remote_task_id: str) -> dict[str, Any]:
        data = await self.post("/admin/research/aiTopicInfo", {"id": int(remote_task_id)})
        return data if isinstance(data, dict) else {}

    async def claim(self, remote_task_id: str) -> None:
        await self.post("/admin/research/startDevAiTopic", {"id": int(remote_task_id)})

    async def download(self, url: str) -> bytes:
        if not self.token:
            await self.login()
        for attempt in range(2):
            headers = {"Authorization": self.token}
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                response = await client.get(url, headers=headers, cookies=self.cookies)
            if attempt == 0:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if is_target_auth_failure(response.status_code, body):
                    await self.login(force=True)
                    continue
            response.raise_for_status()
            return response.content
        raise RuntimeError("题图下载失败")
