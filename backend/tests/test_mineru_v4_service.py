import asyncio
import io
import json
import zipfile

import httpx
import pytest

from app.services.mineru_v4_service import MineruApiError, MineruOptions, MineruV4Service


def test_local_file_uses_official_upload_then_batch_poll(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/file-urls/batch"):
            return httpx.Response(200, json={"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/file"]}})
        if request.url.host == "upload.example":
            return httpx.Response(200)
        if request.url.path.endswith("/extract-results/batch/batch-1"):
            return httpx.Response(200, json={"code": 0, "data": {"extract_result": [{"data_id": "job-1", "state": "done"}]}})
        return httpx.Response(404)

    source = tmp_path / "scan.png"
    source.write_bytes(b"image")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = MineruV4Service(api_token="test-token", client=client)
            batch_id, data_id = await service.submit_file_with_options(source, "job-1", MineruOptions(is_ocr=True))
            result = await service.get_batch_result(batch_id, data_id)
            assert result.status == "done"

    asyncio.run(run())
    payload = json.loads(requests[0].content)
    assert [request.method for request in requests] == ["POST", "PUT", "GET"]
    assert payload["files"] == [{"name": "scan.png", "data_id": "job-1", "is_ocr": True}]
    assert payload["model_version"] == "vlm"
    assert payload["language"] == "ch"
    assert payload["enable_formula"] is True
    assert payload["enable_table"] is True


def test_result_zip_is_extracted_without_path_traversal(tmp_path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.md", "bad")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=archive.getvalue()))) as client:
            service = MineruV4Service(api_token="test-token", client=client)
            with pytest.raises(MineruApiError, match="不安全路径"):
                await service.extract_result_archive("https://download.example/result.zip", tmp_path / "result")

    asyncio.run(run())
