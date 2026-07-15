import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.api.target_system_routes import (
    ConfirmReviewRequest,
    RemoteSelectionRequest,
    claim_selected_tasks,
    confirm_review,
    current_delivery,
    extract_delivery_content,
    mark_delivered,
    mark_filled,
    reserve_next_delivery,
    return_task_to_all,
)
from app.core.database import Base, SessionLocal, engine
from app.main import _sync_target_system_workflow_state, app, ensure_target_system_columns
from app.models.domain import TargetSystemDeliveryLock, TargetSystemTask, Task


def _clear_test_rows(db):
    db.query(TargetSystemDeliveryLock).delete()
    db.query(TargetSystemTask).filter(TargetSystemTask.workflow_task_id.like("target_test_%")).delete(synchronize_session=False)
    db.query(Task).filter(Task.task_id.like("target_test_%")).delete(synchronize_session=False)
    db.commit()


def test_delivery_queue_blocks_until_manual_delivery():
    Base.metadata.create_all(bind=engine)
    ensure_target_system_columns()
    first_task_id = f"target_test_{uuid.uuid4().hex}"
    second_task_id = f"target_test_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        _clear_test_rows(db)
        first = TargetSystemTask(remote_task_id=f"remote_{uuid.uuid4().hex}", status="ready_to_fill", delivery_order=1, workflow_task_id=first_task_id)
        second = TargetSystemTask(remote_task_id=f"remote_{uuid.uuid4().hex}", status="ready_to_fill", delivery_order=2, workflow_task_id=second_task_id)
        db.add_all([
            Task(task_id=first_task_id, thread_id=first_task_id, image_url="", state="completed", final_result="答案一", input_revision=1),
            Task(task_id=second_task_id, thread_id=second_task_id, image_url="", state="completed", final_result="答案二", input_revision=1),
            first,
            second,
        ])
        db.commit()
        first_id, second_id = first.id, second.id

    assert reserve_next_delivery()["item_id"] == first_id
    with pytest.raises(HTTPException) as blocked:
        reserve_next_delivery()
    assert blocked.value.status_code == 409

    mark_filled(first_id, None)
    mark_delivered(first_id)
    assert reserve_next_delivery()["item_id"] == second_id

    with SessionLocal() as db:
        db.query(TargetSystemDeliveryLock).delete()
        db.query(TargetSystemTask).filter(TargetSystemTask.id.in_([first_id, second_id])).delete(synchronize_session=False)
        db.query(Task).filter(Task.task_id.in_([first_task_id, second_task_id])).delete(synchronize_session=False)
        db.commit()


def test_return_to_all_releases_delivery_lock_and_abandons_running_workflow():
    Base.metadata.create_all(bind=engine)
    ensure_target_system_columns()
    task_id = f"target_test_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        _clear_test_rows(db)
        item = TargetSystemTask(remote_task_id=f"remote_{uuid.uuid4().hex}", status="awaiting_user_submit", workflow_task_id=task_id, delivery_order=1, exam_point="考点")
        db.add_all([
            Task(task_id=task_id, thread_id=task_id, image_url="", state="solving", input_revision=1),
            item,
            TargetSystemDeliveryLock(id=1, target_task_id=None),
        ])
        db.commit()
        db.query(TargetSystemDeliveryLock).filter(TargetSystemDeliveryLock.id == 1).update({"target_task_id": item.id})
        db.commit()
        item_id = item.id

    result = return_task_to_all(item_id)
    assert result["status"] == "discovered"
    assert result["workflow_task_id"] is None
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == task_id).first()
        lock = db.query(TargetSystemDeliveryLock).filter(TargetSystemDeliveryLock.id == 1).first()
        assert task and task.state == "abandoned"
        assert lock and lock.target_task_id is None
        db.query(TargetSystemDeliveryLock).delete()
        db.query(TargetSystemTask).filter(TargetSystemTask.id == item_id).delete(synchronize_session=False)
        db.query(Task).filter(Task.task_id == task_id).delete(synchronize_session=False)
        db.commit()


def test_delivery_content_splits_exam_point_from_answer_body():
    task = Task(
        task_id=f"target_test_{uuid.uuid4().hex}",
        thread_id="delivery-content",
        image_url="",
        state="completed",
        answer_preview="【正解】\n答案正文\n\n【考点延伸】\n知识点9 振动：9.1 简谐振动",
        final_result="完整结果",
        input_revision=1,
    )
    answer_body, exam_point = extract_delivery_content(task)
    assert answer_body == "【正解】\n答案正文"
    assert exam_point == "知识点9 振动：9.1 简谐振动"


def test_confirm_review_requires_exam_point_and_current_delivery_hides_it_from_answer():
    Base.metadata.create_all(bind=engine)
    ensure_target_system_columns()
    valid_task_id = f"target_test_{uuid.uuid4().hex}"
    invalid_task_id = f"target_test_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        _clear_test_rows(db)
        valid = TargetSystemTask(remote_task_id=f"remote_{uuid.uuid4().hex}", status="review_pending", workflow_task_id=valid_task_id)
        invalid = TargetSystemTask(remote_task_id=f"remote_{uuid.uuid4().hex}", status="review_pending", workflow_task_id=invalid_task_id)
        db.add_all([
            Task(task_id=valid_task_id, thread_id=valid_task_id, image_url="", state="completed", final_result="【正解】\n正文\n【考点延伸】\n知识点1 力学：1.1 振动", answer_preview="【正解】\n正文\n【考点延伸】\n知识点1 力学：1.1 振动", input_revision=1),
            Task(task_id=invalid_task_id, thread_id=invalid_task_id, image_url="", state="completed", final_result="【正解】\n正文", input_revision=1),
            valid,
            invalid,
        ])
        db.commit()
        valid_id, invalid_id = valid.id, invalid.id

    result = confirm_review(valid_id, ConfirmReviewRequest(exam_point="不应覆盖自动提取值"))
    assert result["status"] == "ready_to_fill"
    with SessionLocal() as db:
        item = db.query(TargetSystemTask).filter(TargetSystemTask.id == valid_id).first()
        assert item and item.exam_point == "知识点1 力学：1.1 振动"
        item.status = "filling"
        db.add(TargetSystemDeliveryLock(id=1, target_task_id=valid_id))
        db.commit()

    active = current_delivery()["item"]
    assert active["answer_markdown"] == "【正解】\n正文"
    assert active["exam_point"] == "知识点1 力学：1.1 振动"

    with pytest.raises(HTTPException) as missing_marker:
        confirm_review(invalid_id, ConfirmReviewRequest())
    assert missing_marker.value.status_code == 409

    with SessionLocal() as db:
        db.query(TargetSystemDeliveryLock).delete()
        db.query(TargetSystemTask).filter(TargetSystemTask.id.in_([valid_id, invalid_id])).delete(synchronize_session=False)
        db.query(Task).filter(Task.task_id.in_([valid_task_id, invalid_task_id])).delete(synchronize_session=False)
        db.commit()


def test_target_system_completion_syncs_to_review_pending():
    Base.metadata.create_all(bind=engine)
    ensure_target_system_columns()
    task_id = f"target_test_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        _clear_test_rows(db)
        item = TargetSystemTask(remote_task_id=f"remote_{uuid.uuid4().hex}", status="solving", workflow_task_id=task_id)
        task = Task(task_id=task_id, thread_id=task_id, image_url="", state="completed", final_result="完整答案", source_kind="target_system", input_revision=1)
        db.add_all([item, task])
        db.flush()
        _sync_target_system_workflow_state(db, task)
        db.commit()
        assert item.status == "review_pending"
        assert item.error_message is None
        db.delete(item)
        db.delete(task)
        db.commit()


def test_active_routes_are_registered_before_task_id_route():
    paths = [route.path for route in app.routes]
    assert paths.index("/api/tasks/active") < paths.index("/api/tasks/{task_id}")
    assert paths.index("/api/tasks/active/list") < paths.index("/api/tasks/{task_id}")


def test_claim_starts_each_workflow_before_later_imports_finish(monkeypatch):
    Base.metadata.create_all(bind=engine)
    ensure_target_system_columns()
    remote_ids = [f"remote_{uuid.uuid4().hex}", f"remote_{uuid.uuid4().hex}"]
    events: list[str] = []
    started: list[str] = []

    class FakeTargetClient:
        async def claim(self, remote_task_id):
            events.append(f"claim:{remote_task_id}")

        async def detail(self, remote_task_id):
            await asyncio.sleep(0)
            events.append(f"detail:{remote_task_id}")
            return {"id": remote_task_id, "topic_text": "题干"}

        async def download(self, _url):
            return b""

    async def fake_workflow(task_id, *_args):
        started.append(task_id)
        events.append(f"workflow:{task_id}")

    monkeypatch.setattr("app.api.target_system_routes.TargetSystemClient", FakeTargetClient)
    monkeypatch.setattr("app.main.run_agent_workflow_async", fake_workflow)

    with SessionLocal() as db:
        _clear_test_rows(db)
        db.add_all([TargetSystemTask(remote_task_id=remote_id, status="selected") for remote_id in remote_ids])
        db.commit()

    async def run_claim():
        result = await claim_selected_tasks(RemoteSelectionRequest(remote_task_ids=remote_ids))
        await asyncio.sleep(0)
        return result

    result = asyncio.run(run_claim())
    assert len(result["started_task_ids"]) == 2
    assert len(started) == 2
    first_workflow_index = next(index for index, event in enumerate(events) if event.startswith("workflow:"))
    detail_indices = [index for index, event in enumerate(events) if event.startswith("detail:")]
    assert first_workflow_index < max(detail_indices)

    with SessionLocal() as db:
        db.query(TargetSystemTask).filter(TargetSystemTask.remote_task_id.in_(remote_ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.task_id.in_(result["started_task_ids"])).delete(synchronize_session=False)
        db.commit()
