"""
MinerU 精准解析 API v4 服务

提供高精度文档解析，支持 PDF、DOC、PPT、图片等格式
需要 Token 认证，每天 2000 页高优先级额度
"""
import asyncio
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass
from typing import Optional

import requests

MINERU_V4_API_BASE = "https://mineru.net/api/v4"


@dataclass(frozen=True)
class MineruV4ParseResult:
    """MinerU v4 解析结果"""
    task_id: str
    status: str  # pending, running, done, failed
    state: str  # 同 status
    markdown_url: Optional[str] = None
    markdown_content: Optional[str] = None
    full_zip_url: Optional[str] = None
    error_msg: Optional[str] = None
    extract_progress: Optional[dict] = None  # {"extracted_pages": int, "total_pages": int}


class MineruV4Service:
    """MinerU v4 精准解析服务"""

    def __init__(
        self,
        api_token: Optional[str] = None,
        poll_interval: float = 3.0,
        max_wait: float = 600.0,
    ):
        """
        初始化 MinerU v4 服务

        Args:
            api_token: MinerU API Token（从环境变量 MINERU_API_TOKEN 获取）
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）
        """
        self.api_token = api_token or os.environ.get("MINERU_API_TOKEN", "")
        if not self.api_token:
            raise ValueError("MINERU_API_TOKEN 环境变量未设置")
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def _get_headers(self) -> dict:
        """获取请求头"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}",
        }

    async def parse_url(
        self,
        url: str,
        model_version: str = "vlm",
    ) -> str:
        """
        通过 URL 提交解析任务

        Args:
            url: 远程文件 URL
            model_version: 模型版本，默认 vlm

        Returns:
            task_id
        """
        resp = requests.post(
            f"{MINERU_V4_API_BASE}/extract/task",
            headers=self._get_headers(),
            json={"url": url, "model_version": model_version},
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise ValueError(f"MinerU v4 提交任务失败: {result.get('msg')}")
        return result["data"]["task_id"]

    async def get_result(self, task_id: str) -> MineruV4ParseResult:
        """
        查询 URL 解析任务结果（用于 parse_url 方式）

        Args:
            task_id: 任务 ID

        Returns:
            MineruV4ParseResult
        """
        resp = requests.get(
            f"{MINERU_V4_API_BASE}/extract/task/{task_id}",
            headers=self._get_headers(),
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise ValueError(f"MinerU v4 查询失败: {result.get('msg')}")

        data = result["data"]
        return MineruV4ParseResult(
            task_id=data["task_id"],
            status=data["state"],
            state=data["state"],
            full_zip_url=data.get("full_zip_url"),
            error_msg=data.get("err_msg"),
            extract_progress=data.get("extract_progress"),
        )

    async def get_batch_result(self, batch_id: str) -> MineruV4ParseResult:
        """
        查询批量文件解析结果（用于 file-upload 方式）

        Args:
            batch_id: 批量任务 ID

        Returns:
            MineruV4ParseResult
        """
        resp = requests.get(
            f"{MINERU_V4_API_BASE}/extract-results/batch/{batch_id}",
            headers=self._get_headers(),
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise ValueError(f"MinerU v4 批量查询失败: {result.get('msg')}")

        data = result["data"]["extract_result"][0]
        print(f"[DEBUG get_batch_result] batch_id={batch_id}, raw_data={data}")
        return MineruV4ParseResult(
            task_id=batch_id,
            status=data["state"],
            state=data["state"],
            full_zip_url=data.get("full_zip_url"),
            error_msg=data.get("err_msg"),
            extract_progress=data.get("extract_progress"),
        )

    async def wait_for_completion(
        self,
        task_id: str,
        poll_interval: Optional[float] = None,
        max_wait: Optional[float] = None,
    ) -> MineruV4ParseResult:
        """
        阻塞等待解析完成

        Args:
            task_id: 任务 ID
            poll_interval: 轮询间隔
            max_wait: 最大等待时间

        Returns:
            MineruV4ParseResult
        """
        interval = poll_interval or self.poll_interval
        timeout = max_wait or self.max_wait

        state_labels = {
            "pending": "排队中",
            "running": "解析中",
            "done": "解析完成",
            "failed": "解析失败",
        }

        start = time.time()
        while time.time() - start < timeout:
            result = await self.get_result(task_id)
            elapsed = int(time.time() - start)

            if result.status == "done":
                print(f"[{elapsed}s] MinerU v4 解析完成")
                return result

            if result.status == "failed":
                print(f"[{elapsed}s] MinerU v4 解析失败: {result.error_msg}")
                return result

            label = state_labels.get(result.status, result.status)
            if result.extract_progress:
                ep = result.extract_progress
                print(f"[{elapsed}s] {label}... ({ep.get('extracted_pages', 0)}/{ep.get('total_pages', '?')})")
            else:
                print(f"[{elapsed}s] {label}...")

            await asyncio.sleep(interval)

        final_result = await self.get_result(task_id)
        print(f"轮询超时 ({timeout}s)，状态: {final_result.status}")
        return final_result

    async def wait_for_batch_completion(
        self,
        batch_id: str,
        poll_interval: Optional[float] = None,
        max_wait: Optional[float] = None,
    ) -> MineruV4ParseResult:
        """
        阻塞等待批量文件解析完成

        Args:
            batch_id: 批量任务 ID
            poll_interval: 轮询间隔
            max_wait: 最大等待时间

        Returns:
            MineruV4ParseResult
        """
        interval = poll_interval or self.poll_interval
        timeout = max_wait or self.max_wait

        state_labels = {
            "pending": "排队中",
            "running": "解析中",
            "done": "解析完成",
            "failed": "解析失败",
        }

        start = time.time()
        while time.time() - start < timeout:
            result = await self.get_batch_result(batch_id)
            elapsed = int(time.time() - start)

            if result.status == "done":
                print(f"[{elapsed}s] MinerU v4 批量解析完成")
                return result

            if result.status == "failed":
                print(f"[{elapsed}s] MinerU v4 批量解析失败: {result.error_msg}")
                return result

            label = state_labels.get(result.status, result.status)
            if result.extract_progress:
                ep = result.extract_progress
                print(f"[{elapsed}s] {label}... ({ep.get('extracted_pages', 0)}/{ep.get('total_pages', '?')})")
            else:
                print(f"[{elapsed}s] {label}...")

            await asyncio.sleep(interval)

        final_result = await self.get_batch_result(batch_id)
        print(f"轮询超时 ({timeout}s)，状态: {final_result.status}")
        return final_result

    async def download_and_extract_markdown(self, zip_url: str) -> str:
        """
        下载 zip 文件并提取 markdown 内容

        Args:
            zip_url: zip 文件 URL

        Returns:
            markdown 文本内容
        """
        # 下载 zip
        resp = requests.get(zip_url, timeout=120)
        resp.raise_for_status()

        # 解压到临时目录
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "result.zip")
            with open(zip_path, "wb") as f:
                f.write(resp.content)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)

            # 查找 markdown 文件
            for root, _, files in os.walk(tmpdir):
                for fname in files:
                    if fname.endswith(".md"):
                        fpath = os.path.join(root, fname)
                        with open(fpath, "r", encoding="utf-8") as f:
                            return f.read()

        raise ValueError("zip 中未找到 markdown 文件")

    async def download_and_extract_images(self, zip_url: str) -> dict[str, str]:
        """
        下载 zip 文件并提取所有图片，转换为 base64 data URL

        Args:
            zip_url: zip 文件 URL

        Returns:
            dict: {relative_path: base64_data_url}，例如 {"images/xxx.jpg": "data:image/jpeg;base64,..."}
        """
        import base64

        resp = requests.get(zip_url, timeout=120)
        resp.raise_for_status()

        images_dict: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "result.zip")
            with open(zip_path, "wb") as f:
                f.write(resp.content)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)

                # 遍历所有文件，找到 images 目录下的图片
                for name in zf.namelist():
                    if name.startswith("images/") and not name.endswith("/"):
                        # 这是一个图片文件
                        file_data = zf.read(name)
                        # 根据扩展名确定 MIME 类型
                        ext = name.lower().split(".")[-1]
                        mime_types = {
                            "jpg": "image/jpeg",
                            "jpeg": "image/jpeg",
                            "png": "image/png",
                            "gif": "image/gif",
                            "webp": "image/webp",
                            "bmp": "image/bmp",
                        }
                        mime_type = mime_types.get(ext, "image/jpeg")
                        # 转换为 base64
                        b64_data = base64.b64encode(file_data).decode("utf-8")
                        data_url = f"data:{mime_type};base64,{b64_data}"
                        images_dict[name] = data_url

        return images_dict

    async def parse_url_and_wait(
        self,
        url: str,
        model_version: str = "vlm",
        poll_interval: Optional[float] = None,
        max_wait: Optional[float] = None,
    ) -> MineruV4ParseResult:
        """
        提交 URL 解析并等待完成

        Args:
            url: 远程文件 URL
            model_version: 模型版本
            poll_interval: 轮询间隔
            max_wait: 最大等待时间

        Returns:
            包含 markdown_content 的 MineruV4ParseResult
        """
        task_id = await self.parse_url(url, model_version)
        print(f"任务已提交: {task_id}")

        result = await self.wait_for_completion(task_id, poll_interval, max_wait)

        if result.status == "done" and result.full_zip_url:
            result = MineruV4ParseResult(
                task_id=result.task_id,
                status=result.status,
                state=result.state,
                full_zip_url=result.full_zip_url,
                markdown_content=await self.download_and_extract_markdown(result.full_zip_url),
                error_msg=result.error_msg,
            )

        return result


# 全局单例
_mineru_v4_service: Optional[MineruV4Service] = None


def get_mineru_v4_service() -> MineruV4Service:
    """获取 MinerU v4 服务单例"""
    global _mineru_v4_service
    if _mineru_v4_service is None:
        _mineru_v4_service = MineruV4Service()
    return _mineru_v4_service
