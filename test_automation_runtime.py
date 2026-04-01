import os
import sys
import asyncio

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

os.environ.setdefault("AUTOMATION_USE_MOCK", "1")

from app.automation.models import AutomationLog, AutomationTask
from app.automation.schemas import StartSessionReq
from app.automation.service import AutomationService
from app.core.database import Base, engine


def _reset_automation_tables() -> None:
    AutomationLog.__table__.drop(bind=engine, checkfirst=True)
    AutomationTask.__table__.drop(bind=engine, checkfirst=True)
    Base.metadata.create_all(bind=engine)


async def _wait_until_idle(
    service: AutomationService, run_id: str, timeout_seconds: int = 20
):
    for _ in range(timeout_seconds * 10):
        run = service.get_run(run_id)
        if run.state in {"idle", "stopped"}:
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("run did not become idle in time")


def test_runtime_flow_with_hard_stop_and_review():
    async def _run():
        _reset_automation_tables()
        service = AutomationService()

        run = await service.start_session(
            StartSessionReq(username="demo", password="demo", mode="headless")
        )
        run_id = run.run_id

        # 直接调用，避免异步触发带来的轮询竞态
        await service.start_scan(run_id)

        total, discovered_items = service.list_tasks(
            run_id=run_id,
            status="discovered",
            school=None,
            page=1,
            page_size=50,
        )
        assert total > 0 and len(discovered_items) > 0

        task_ids = [item.task_id for item in discovered_items]
        selected_count = await service.select_tasks(run_id, task_ids)
        assert selected_count > 0

        await service.start_grab(run_id)

        # stop 接口必须可调用并进入 stopped/idle。
        await service.stop(run_id)

        status = service.get_run(run_id)
        assert status.state in {"stopped", "idle"}

    asyncio.run(_run())


if __name__ == "__main__":
    test_runtime_flow_with_hard_stop_and_review()
    print("automation runtime smoke test passed")
