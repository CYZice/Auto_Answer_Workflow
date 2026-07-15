"""MinerU 精准解析 API v4 客户端。

本地文件严格遵循官方链路：申请签名上传地址 -> PUT 文件 -> 轮询批量结果。
上传完成后 MinerU 会自动提交解析任务，不能再调用一次提交接口。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import httpx
import yaml

MINERU_V4_API_BASE = "https://mineru.net/api/v4"
logger = logging.getLogger(__name__)


class MineruApiError(ValueError):
    """对调用方安全的 MinerU 错误。"""


@dataclass(frozen=True)
class MineruOptions:
    model_version: str = "vlm"
    language: str = "ch"
    enable_formula: bool = True
    enable_table: bool = True
    is_ocr: bool = False
    page_ranges: Optional[str] = None
    extra_formats: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "language": self.language,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "is_ocr": self.is_ocr,
            "page_ranges": self.page_ranges,
            "extra_formats": list(self.extra_formats),
        }


@dataclass(frozen=True)
class MineruV4ParseResult:
    task_id: str
    status: str
    state: str
    data_id: Optional[str] = None
    markdown_url: Optional[str] = None
    markdown_content: Optional[str] = None
    full_zip_url: Optional[str] = None
    error_msg: Optional[str] = None
    extract_progress: Optional[dict] = None


@dataclass(frozen=True)
class MineruExtractedResult:
    markdown_content: str
    markdown_path: Path
    result_dir: Path
    files: tuple[str, ...]


def _private_settings() -> dict[str, Any]:
    config_file = _private_settings_path()
    if not config_file.exists():
        return {}
    try:
        value = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        return value if isinstance(value, dict) else {}
    except (OSError, yaml.YAMLError):
        logger.warning("MinerU 私有配置无法读取")
        return {}


def _private_settings_path() -> Path:
    return Path(os.getenv("CONFIG_DIR", "/app/config")) / "mineru.local.private.yaml"


def _mask_api_token(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:3]}{'*' * min(12, len(token) - 7)}{token[-4:]}"


def _effective_settings() -> tuple[str, str]:
    settings = _private_settings()
    if "api_token" in settings:
        token = str(settings["api_token"] or "").strip()
    elif "token" in settings:
        token = str(settings["token"] or "").strip()
    else:
        token = str(os.getenv("MINERU_API_TOKEN") or "").strip()
    base_url = str(
        settings.get("api_base_url")
        or os.getenv("MINERU_API_BASE_URL")
        or MINERU_V4_API_BASE
    ).strip().rstrip("/")
    return token, base_url


def public_mineru_settings() -> dict[str, Any]:
    token, base_url = _effective_settings()
    return {
        "base_url": base_url,
        "api_token_masked": _mask_api_token(token),
        "api_token_configured": bool(token),
    }


def update_mineru_settings(payload: dict[str, Any]) -> dict[str, Any]:
    path = _private_settings_path()
    settings = _private_settings()
    if "base_url" in payload and payload["base_url"] is not None:
        settings["api_base_url"] = str(payload["base_url"]).strip()
    if payload.get("clear_api_token") is True:
        settings["api_token"] = ""
    elif str(payload.get("api_token") or "").strip():
        settings["api_token"] = str(payload["api_token"]).strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(settings, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return public_mineru_settings()


def mineru_is_configured() -> bool:
    token, _ = _effective_settings()
    return bool(token)


class MineruV4Service:
    """官方 v4 精准解析客户端；不记录令牌、签名 URL 或解析正文。"""

    def __init__(
        self,
        api_token: Optional[str] = None,
        api_base_url: Optional[str] = None,
        poll_interval: float = 3.0,
        max_wait: float = 600.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        configured_token, configured_base_url = _effective_settings()
        self.api_token = (api_token or configured_token).strip()
        if not self.api_token:
            raise MineruApiError("未配置 MinerU API Token；请在服务端私有配置中填写")
        self.api_base_url = ((api_base_url or configured_base_url).strip().rstrip("/"))
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}

    async def _api_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, f"{self.api_base_url}{path}", headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            raise MineruApiError(f"MinerU 网络请求失败：{exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            if response.status_code in {401, 403}:
                raise MineruApiError("MinerU Token 无效或已过期")
            if response.status_code == 429:
                raise MineruApiError("MinerU 请求过于频繁，请稍后重试")
            raise MineruApiError(f"MinerU 服务返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise MineruApiError("MinerU 返回了非 JSON 响应") from exc
        if payload.get("code") != 0:
            raise MineruApiError(f"MinerU 请求失败：{payload.get('msg') or '未知错误'}")
        return payload.get("data") or {}

    async def submit_file_with_options(
        self, file_path: str | Path, data_id: str, options: Optional[MineruOptions] = None
    ) -> tuple[str, str]:
        path = Path(file_path)
        if not path.is_file():
            raise MineruApiError("MinerU 输入文件不存在")
        options = options or MineruOptions()
        file_item: dict[str, Any] = {"name": path.name, "data_id": data_id, "is_ocr": options.is_ocr}
        if options.page_ranges:
            file_item["page_ranges"] = options.page_ranges
        payload: dict[str, Any] = {
            "files": [file_item],
            "model_version": options.model_version,
            "language": options.language,
            "enable_formula": options.enable_formula,
            "enable_table": options.enable_table,
        }
        if options.extra_formats:
            payload["extra_formats"] = list(options.extra_formats)
        data = await self._api_json("POST", "/file-urls/batch", json=payload)
        batch_id = data.get("batch_id")
        upload_urls = data.get("file_urls") or []
        if not isinstance(batch_id, str) or not upload_urls:
            raise MineruApiError("MinerU 未返回有效上传地址")
        try:
            # 官方要求 PUT 签名地址时不设置 Content-Type。
            response = await self._client.put(upload_urls[0], content=path.read_bytes())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MineruApiError(f"MinerU 文件上传失败：{exc.__class__.__name__}") from exc
        logger.info("MinerU 文件已上传 batch_id=%s data_id=%s", batch_id, data_id)
        return batch_id, data_id

    async def submit_file(self, file_path: str | Path) -> str:
        batch_id, _ = await self.submit_file_with_options(file_path, "upload")
        return batch_id

    async def parse_file_and_wait(self, file_path: str | Path) -> MineruV4ParseResult:
        batch_id = await self.submit_file(file_path)
        result = await self.wait_for_batch_completion(batch_id)
        if result.status == "done" and result.full_zip_url:
            extracted = await self.extract_result_archive(result.full_zip_url)
            return MineruV4ParseResult(**{**result.__dict__, "markdown_content": extracted.markdown_content})
        return result

    async def parse_url(self, url: str, model_version: str = "vlm") -> str:
        data = await self._api_json("POST", "/extract/task", json={"url": url, "model_version": model_version})
        task_id = data.get("task_id")
        if not isinstance(task_id, str):
            raise MineruApiError("MinerU 未返回 task_id")
        return task_id

    @staticmethod
    def _result(task_id: str, data: dict[str, Any]) -> MineruV4ParseResult:
        state = str(data.get("state") or "pending")
        return MineruV4ParseResult(
            task_id=task_id,
            status=state,
            state=state,
            data_id=data.get("data_id"),
            full_zip_url=data.get("full_zip_url"),
            error_msg=data.get("err_msg"),
            extract_progress=data.get("extract_progress"),
        )

    async def get_result(self, task_id: str) -> MineruV4ParseResult:
        return self._result(task_id, await self._api_json("GET", f"/extract/task/{task_id}"))

    async def get_batch_result(self, batch_id: str, data_id: Optional[str] = None) -> MineruV4ParseResult:
        data = await self._api_json("GET", f"/extract-results/batch/{batch_id}")
        results = data.get("extract_result") or []
        selected = next((item for item in results if data_id and item.get("data_id") == data_id), results[0] if results else None)
        if not isinstance(selected, dict):
            raise MineruApiError("MinerU 批量结果中没有文件状态")
        return self._result(batch_id, selected)

    async def wait_for_completion(self, task_id: str, poll_interval: Optional[float] = None, max_wait: Optional[float] = None) -> MineruV4ParseResult:
        return await self._wait(lambda: self.get_result(task_id), poll_interval, max_wait)

    async def wait_for_batch_completion(self, batch_id: str, poll_interval: Optional[float] = None, max_wait: Optional[float] = None) -> MineruV4ParseResult:
        return await self._wait(lambda: self.get_batch_result(batch_id), poll_interval, max_wait)

    async def _wait(self, get_result: Any, poll_interval: Optional[float], max_wait: Optional[float]) -> MineruV4ParseResult:
        deadline = asyncio.get_running_loop().time() + (max_wait or self.max_wait)
        while True:
            result = await get_result()
            if result.status in {"done", "failed"} or asyncio.get_running_loop().time() >= deadline:
                return result
            await asyncio.sleep(poll_interval or self.poll_interval)

    @staticmethod
    def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> tuple[str, ...]:
        files: list[str] = []
        destination.mkdir(parents=True, exist_ok=True)
        for info in zf.infolist():
            relative = PurePosixPath(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise MineruApiError("MinerU 结果压缩包包含不安全路径")
            target = (destination / Path(*relative.parts)).resolve()
            if target != destination.resolve() and destination.resolve() not in target.parents:
                raise MineruApiError("MinerU 结果压缩包路径非法")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            files.append(str(target.relative_to(destination)))
        return tuple(files)

    async def extract_result_archive(self, zip_url: str, destination: Optional[str | Path] = None) -> MineruExtractedResult:
        try:
            response = await self._client.get(zip_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MineruApiError(f"MinerU 结果下载失败：{exc.__class__.__name__}") from exc
        root = Path(destination) if destination else Path(tempfile.mkdtemp(prefix="mineru-result-"))
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                files = self._safe_extract(archive, root)
        except zipfile.BadZipFile as exc:
            raise MineruApiError("MinerU 返回的结果压缩包损坏") from exc
        markdown_files = sorted(root.rglob("*.md"), key=lambda item: (item.name != "full.md", str(item)))
        if not markdown_files:
            raise MineruApiError("MinerU 结果中未找到 Markdown")
        markdown_path = markdown_files[0]
        return MineruExtractedResult(markdown_path.read_text(encoding="utf-8"), markdown_path, root, files)

    async def download_and_extract_markdown(self, zip_url: str) -> str:
        return (await self.extract_result_archive(zip_url)).markdown_content

    async def download_and_extract_images(self, zip_url: str) -> dict[str, str]:
        extracted = await self.extract_result_archive(zip_url)
        mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
        images: dict[str, str] = {}
        for path in extracted.result_dir.rglob("*"):
            if path.suffix.lower() in mime_types and path.is_file():
                images[str(path.relative_to(extracted.result_dir)).replace("\\", "/")] = f"data:{mime_types[path.suffix.lower()]};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        return images

    async def parse_url_and_wait(self, url: str, model_version: str = "vlm", poll_interval: Optional[float] = None, max_wait: Optional[float] = None) -> MineruV4ParseResult:
        task_id = await self.parse_url(url, model_version)
        result = await self.wait_for_completion(task_id, poll_interval, max_wait)
        if result.status == "done" and result.full_zip_url:
            extracted = await self.extract_result_archive(result.full_zip_url)
            return MineruV4ParseResult(**{**result.__dict__, "markdown_content": extracted.markdown_content})
        return result


_mineru_v4_service: Optional[MineruV4Service] = None
_mineru_v4_service_config: tuple[str, str] | None = None


def get_mineru_v4_service() -> MineruV4Service:
    global _mineru_v4_service, _mineru_v4_service_config
    config = _effective_settings()
    if _mineru_v4_service is None or _mineru_v4_service_config != config:
        _mineru_v4_service = MineruV4Service(
            api_token=config[0], api_base_url=config[1]
        )
        _mineru_v4_service_config = config
    return _mineru_v4_service
