from pathlib import Path

from app.services.mineru_jobs import create_mineru_job, poll_mineru_job


async def parse_local_file_with_mineru(
    source_path: str | Path,
) -> str:
    """工作台共用入口：所有来源都落入持久化 v4 任务，再读取统一 Markdown。"""
    path = Path(source_path)
    job, _ = await create_mineru_job(path.name, None, path.read_bytes())
    await poll_mineru_job(job.job_id)
    from app.services.mineru_jobs import refresh_mineru_job, job_to_dict
    result = job_to_dict(await refresh_mineru_job(job.job_id))
    if result["status"] != "done" or not result["markdown_content"]:
        raise ValueError(result["error_message"] or f"MinerU 解析未完成：{result['status']}")
    return result["markdown_content"]
