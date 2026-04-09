"""
MinerU API 封装服务

提供文件上传解析、URL 解析、轮询查询等功能
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import requests

MINERU_API_BASE_URL = "https://mineru.net/api/v1/agent"


@dataclass(frozen=True)
class MineruParseResult:
    """MinerU 解析结果"""
    mineru_task_id: str
    status: str  # pending, uploading, waiting-file, running, done, failed
    markdown_url: Optional[str] = None
    markdown_content: Optional[str] = None
    error_msg: Optional[str] = None
    error_code: Optional[int] = None


class MineruService:
    """MinerU API 封装服务"""

    def __init__(
        self,
        api_base_url: str = MINERU_API_BASE_URL,
        poll_interval: float = 2.0,
        max_wait: float = 300.0,
    ):
        self.api_base_url = api_base_url
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    async def parse_file(
        self,
        file_content: bytes,
        filename: str,
        language: str = "ch",
        enable_table: bool = True,
        is_ocr: bool = False,
        enable_formula: bool = True,
        page_range: Optional[str] = None,
    ) -> str:
        """
        上传文件获取 task_id

        Args:
            file_content: 文件二进制内容
            filename: 文件名（含扩展名）
            language: 解析语言，默认 ch
            enable_table: 是否开启表格识别
            is_ocr: 是否开启 OCR
            enable_formula: 是否开启公式识别
            page_range: 页码范围，如 "1-10"

        Returns:
            mineru_task_id
        """
        # 第一步：获取签名上传 URL
        data = {
            "file_name": filename,
            "language": language,
            "enable_table": enable_table,
            "is_ocr": is_ocr,
            "enable_formula": enable_formula,
        }
        if page_range:
            data["page_range"] = page_range

        resp = requests.post(
            f"{self.api_base_url}/parse/file",
            json=data,
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise ValueError(f"MinerU 获取上传链接失败: {result.get('msg')}")

        mineru_task_id = result["data"]["task_id"]
        file_url = result["data"]["file_url"]

        # 第二步：PUT 上传文件到 OSS
        put_resp = requests.put(file_url, data=file_content, timeout=60)
        if put_resp.status_code not in (200, 201):
            raise ValueError(f"文件上传失败, HTTP {put_resp.status_code}")

        return mineru_task_id

    async def parse_url(
        self,
        url: str,
        language: str = "ch",
        enable_table: bool = True,
        is_ocr: bool = False,
        enable_formula: bool = True,
        page_range: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> str:
        """
        提交 URL 解析任务

        Args:
            url: 远程文件 URL
            language: 解析语言
            enable_table: 是否开启表格识别
            is_ocr: 是否开启 OCR
            enable_formula: 是否开启公式识别
            page_range: 页码范围
            file_name: 文件名（可选）

        Returns:
            mineru_task_id
        """
        data = {
            "url": url,
            "language": language,
            "enable_table": enable_table,
            "is_ocr": is_ocr,
            "enable_formula": enable_formula,
        }
        if page_range:
            data["page_range"] = page_range
        if file_name:
            data["file_name"] = file_name

        resp = requests.post(
            f"{self.api_base_url}/parse/url",
            json=data,
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise ValueError(f"MinerU URL 解析提交失败: {result.get('msg')}")

        return result["data"]["task_id"]

    async def get_result(self, mineru_task_id: str) -> MineruParseResult:
        """
        查询解析状态和结果

        Args:
            mineru_task_id: MinerU 任务 ID

        Returns:
            MineruParseResult
        """
        resp = requests.get(
            f"{self.api_base_url}/parse/{mineru_task_id}",
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise ValueError(f"MinerU 查询失败: {result.get('msg')}")

        data = result["data"]
        return MineruParseResult(
            mineru_task_id=data["task_id"],
            status=data["state"],
            markdown_url=data.get("markdown_url"),
            error_msg=data.get("err_msg"),
            error_code=data.get("err_code"),
        )

    async def wait_for_completion(
        self,
        mineru_task_id: str,
        poll_interval: Optional[float] = None,
        max_wait: Optional[float] = None,
    ) -> MineruParseResult:
        """
        阻塞等待解析完成

        Args:
            mineru_task_id: MinerU 任务 ID
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）

        Returns:
            MineruParseResult
        """
        interval = poll_interval or self.poll_interval
        timeout = max_wait or self.max_wait

        state_labels = {
            "uploading": "文件上传中",
            "pending": "排队中",
            "running": "解析中",
            "waiting-file": "等待文件上传",
        }

        start = time.time()
        while time.time() - start < timeout:
            result = await self.get_result(mineru_task_id)
            elapsed = int(time.time() - start)

            if result.status == "done":
                # 下载 markdown 内容
                if result.markdown_url:
                    result = MineruParseResult(
                        mineru_task_id=result.mineru_task_id,
                        status=result.status,
                        markdown_url=result.markdown_url,
                        markdown_content=await self.download_markdown(result.markdown_url),
                        error_msg=result.error_msg,
                        error_code=result.error_code,
                    )
                print(f"[{elapsed}s] MinerU 解析完成")
                return result

            if result.status == "failed":
                print(f"[{elapsed}s] MinerU 解析失败: {result.error_msg}")
                return result

            label = state_labels.get(result.status, result.status)
            print(f"[{elapsed}s] {label}...")
            await asyncio.sleep(interval)

        # 超时
        final_result = await self.get_result(mineru_task_id)
        print(f"轮询超时 ({timeout}s)，状态: {final_result.status}")
        return final_result

    async def download_markdown(self, markdown_url: str) -> str:
        """
        下载 Markdown 内容

        Args:
            markdown_url: Markdown 文件 URL

        Returns:
            Markdown 文本内容
        """
        resp = requests.get(markdown_url, timeout=60)
        resp.raise_for_status()
        return resp.text


# 全局单例
_mineru_service: Optional[MineruService] = None


def get_mineru_service() -> MineruService:
    """获取 MinerU 服务单例"""
    global _mineru_service
    if _mineru_service is None:
        _mineru_service = MineruService()
    return _mineru_service
