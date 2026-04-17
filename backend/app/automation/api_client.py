"""
Xuejie API Client - 基于真实 HTTP API 的无浏览器自动化客户端

API 逆向结果:
- 登录: POST /admin/login  → {code:200, data: {token, info: {developer_id}}}
- 任务列表: POST /admin/research/aiTopicList → {count, list: [...]}
- 任务详情: POST /admin/research/aiTopicInfo
- 抢单: POST /admin/research/startDevAiTopic
- OCR: POST /admin/openai/imgToText (multipart)
- 保存/提交: POST /admin/research/saveAiTopic
- 取消: POST /admin/research/cancelDevAiTopic
- 学校列表: POST /admin/school/list → {count, list: [...]}
- 认证: Authorization: Bearer <token>
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://yy.xuejie.cn"
DEFAULT_TIMEOUT = 30


def _ua() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    )


def _json_headers(token: str) -> dict:
    return {
        "User-Agent": _ua(),
        "Authorization": token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE_URL,
    }


def _file_headers(token: str) -> dict:
    return {
        "User-Agent": _ua(),
        "Authorization": token,
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE_URL,
    }


# ── 数据模型 ──────────────────────────────────────────────────────

@dataclass
class LoginResult:
    token: str
    developer_id: int
    nickname: str


# ── API Client ──────────────────────────────────────────────────────

class XuejieApiClient:
    """
    基于真实抓包结果的 API 客户端。
    支持: 登录、任务列表、任务详情、抢单、OCR、保存答案、取消任务。
    """

    def __init__(self, base_url: str = BASE_URL, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._token: str | None = None
        self._developer_id: int | None = None

    async def close(self) -> None:
        await self._client.aclose()

    # ── 认证 ──────────────────────────────────────────────────────

    async def login(self, username: str, password: str) -> LoginResult:
        """登录获取 token，developer_id 保存在实例中。"""
        resp = await self._client.post(
            f"{self.base_url}/admin/login",
            json={"username": username, "password": password},
            headers={
                "User-Agent": _ua(),
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        code = data.get("code")
        if code != 200 and code != 0:
            raise RuntimeError(f"Login failed: {data.get('message')} (code={code})")

        inner = data.get("data", {})
        token = inner.get("token", "")
        if not token:
            raise RuntimeError(f"Login response missing token: {inner}")

        info = inner.get("info", {})
        dev_id = int(info.get("developer_id", 0) or 0)
        nickname = info.get("nickname", username)

        self._token = token
        self._developer_id = dev_id
        return LoginResult(token=token, developer_id=dev_id, nickname=nickname)

    def _auth(self) -> str:
        if not self._token:
            raise RuntimeError("Not logged in. Call login() first.")
        return self._token

    # ── 通用请求 ─────────────────────────────────────────────────

    async def _post(self, path: str, json_payload: dict | None = None) -> dict:
        resp = await self._client.post(
            f"{self.base_url}{path}",
            json=json_payload,
            headers=_json_headers(self._auth()),
        )
        resp.raise_for_status()
        result = resp.json()
        code = result.get("code")
        if code != 200 and code != 0:
            raise RuntimeError(
                f"{path} failed: {result.get('message') or result.get('msg')} (code={code})"
            )
        return result.get("data", {})

    # ── 学校列表 ─────────────────────────────────────────────────

    async def list_schools(self, is_business: int = 1) -> list[dict]:
        """获取学校列表。返回: [{school_id, school_name, ...}, ...]"""
        raw = await self._post("/admin/school/list", {"is_business": is_business})
        if isinstance(raw, dict):
            return raw.get("list", [])
        elif isinstance(raw, list):
            return raw
        return []

    # ── 任务列表 ─────────────────────────────────────────────────

    async def list_tasks(
        self,
        *,
        school_id: int = 0,
        subject_ids: list[int] | None = None,
        status: int = 0,
        contain_img: int = 1,
        page: int = 1,
        pagesize: int = 20,
        developer_id: int | None = None,
    ) -> list[dict]:
        """
        获取任务列表。status: 0=待开始, 6=已提交审核中, 1=审核通过。
        返回任务列表: [{id, title, school_id, subject_id, status, ...}, ...]
        """
        dev_id = developer_id or self._developer_id
        payload: dict[str, Any] = {
            "school_id": school_id,
            "status": status,
            "contain_img": contain_img,
            "judge_admin_id": "",
            "developer_id": dev_id,
            "page": page,
            "pagesize": pagesize,
        }
        if subject_ids:
            payload["subject_ids"] = subject_ids
            payload["selfCate"] = subject_ids[0]

        raw = await self._post("/admin/research/aiTopicList", payload)
        if isinstance(raw, dict):
            return raw.get("list", [])
        elif isinstance(raw, list):
            return raw
        return []

    async def list_pending_tasks(
        self,
        school_id: int = 0,
        subject_ids: list[int] | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> list[dict]:
        """获取待解题任务 (status=0)"""
        return await self.list_tasks(
            school_id=school_id,
            subject_ids=subject_ids,
            status=0,
            page=page,
            pagesize=pagesize,
        )

    # ── 任务详情 ─────────────────────────────────────────────────

    async def get_task_detail(self, task_id: int) -> dict:
        """获取任务详情，包含完整题目 HTML、答案格式等。"""
        return await self._post("/admin/research/aiTopicInfo", {"id": task_id})

    # ── 抢单 ─────────────────────────────────────────────────────

    async def grab_task(self, task_id: int) -> dict:
        """抢单（开始解题）。成功后才能保存答案。"""
        return await self._post("/admin/research/startDevAiTopic", {"id": task_id})

    # ── 取消任务 ─────────────────────────────────────────────────

    async def cancel_task(self, task_id: int) -> dict:
        """取消已抢单但未提交的任务。"""
        return await self._post("/admin/research/cancelDevAiTopic", {"id": task_id})

    # ── OCR 识别 ─────────────────────────────────────────────────

    async def ocr_image(self, image_source: str) -> str:
        """
        OCR 识别图片。
        image_source: OSS URL (http...) 或本地文件路径。
        返回纯文本。
        """
        token = self._auth()

        if image_source.startswith("http"):
            img_resp = await httpx.AsyncClient(timeout=30).get(
                image_source,
                headers={"User-Agent": _ua(), "Referer": BASE_URL},
            )
            img_resp.raise_for_status()
            image_data = img_resp.content
        else:
            with open(image_source, "rb") as f:
                image_data = f.read()

        files = {"file": ("image.png", image_data, "image/png")}
        data = {"image": base64.b64encode(image_data).decode()}

        resp = await self._client.post(
            f"{self.base_url}/admin/openai/imgToText",
            files=files,
            data=data,
            headers=_file_headers(token),
        )
        resp.raise_for_status()
        result = resp.json()
        code = result.get("code")
        if code != 200 and code != 0:
            raise RuntimeError(f"ocr_image failed: {result.get('message')} (code={code})")

        return result.get("data", {}).get("text", "")

    # ── 保存/提交答案 ─────────────────────────────────────────────

    async def save_answer(
        self,
        task_id: int,
        *,
        topic: str,
        answer: str,
        topic_text: str = "",
        exam_point: str = "",
        status: int = 6,
        is_unlock: int = 0,
        is_dev_submit: int = 1,
    ) -> dict:
        """
        保存答案（同时也是提交接口）。
        status=6 表示提交审核，isDevSubmit=1 表示开发者主动提交。
        """
        payload = {
            "id": task_id,
            "topic": topic,
            "topic_text": topic_text,
            "answer": answer,
            "answer_text": "",
            "topic_right": "<p><br></p>",
            "exam_point": exam_point,
            "is_unlock": is_unlock,
            "status": status,
            "isDevSubmit": is_dev_submit,
        }
        return await self._post("/admin/research/saveAiTopic", payload)

    # ── 完整接题流程 ─────────────────────────────────────────────

    async def solve_and_submit(
        self,
        task_id: int,
        final_markdown: str,
        answer_preview: str = "",
        exam_point: str = "",
    ) -> dict:
        """
        一站式: 抢单 → 构建答案 → 提交。
        final_markdown 来自 Agent workflow（包含题目和答案的 markdown）。
        """
        # 1. 抢单
        await self.grab_task(task_id)

        # 2. 拆分题目和答案
        answer_marker = final_markdown.find("【正解】")
        if answer_marker < 0:
            answer_marker = final_markdown.find("【解析】")

        if answer_marker >= 0:
            topic_text = final_markdown[:answer_marker].strip()
            answer_section = final_markdown[answer_marker:].strip()
        else:
            topic_text = final_markdown.strip()
            answer_section = ""

        # 3. 转换为 HTML
        topic_html = f"<p>{topic_text.replace(chr(10), '<br>')}</p>"
        answer_html = f"<p>{answer_section.replace(chr(10), '<br>')}</p>"

        # 4. 提交
        return await self.save_answer(
            task_id=task_id,
            topic=topic_html,
            answer=answer_html,
            topic_text=topic_text,
            exam_point=exam_point,
            status=6,
            is_dev_submit=1,
        )


# ── 独立工具函数 ─────────────────────────────────────────────────

def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


async def quick_login(username: str, password: str) -> tuple[str, int]:
    """快速登录，返回 (token, developer_id)"""
    client = XuejieApiClient()
    try:
        result = await client.login(username, password)
        return result.token, result.developer_id
    finally:
        await client.close()
