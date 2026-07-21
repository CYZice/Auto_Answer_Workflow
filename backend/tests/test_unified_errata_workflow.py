import asyncio
import copy
import json
import uuid
from pathlib import Path

import pytest
import yaml

from app.core.database import Base, SessionLocal, engine
from app.api.errata_routes import ErrataItemUpdateRequest, update_errata_item
from app.main import admin_delete_task, ensure_task_preview_columns
from app.models.domain import AgentLog, ErrataItem, ErrataJob, Task, TaskArtifact
from app.models.schemas import RuntimeSettingsResponse
from app.models.schemas import ReviewDecision
from app.agent.nodes import llm_client, reviewer
from app.services import runtime_config
from app.services import mineru_v4_service
from app.services.errata_service import run_errata_task


def test_errata_prompt_configs_match_and_require_all_nodes(tmp_path, monkeypatch):
    repository_root = Path(__file__).resolve().parents[2]
    root_config = yaml.safe_load((repository_root / "config" / "prompt_templates.yaml").read_text(encoding="utf-8"))
    backend_config = yaml.safe_load((repository_root / "backend" / "app" / "config" / "prompt_templates.yaml").read_text(encoding="utf-8"))
    assert root_config["templates"]["errata_workflow"] == backend_config["templates"]["errata_workflow"]
    assert "零级暗纹是从接触点起可观察到的第1条暗纹" in root_config["templates"]["errata_workflow"]["prompts"]["reviewer"]["system"]

    prompt_path = tmp_path / "prompt_templates.yaml"
    prompt_path.write_text(yaml.safe_dump(copy.deepcopy(runtime_config.DEFAULT_PROMPT_TEMPLATES), allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(runtime_config, "PROMPT_TEMPLATES_PATH", prompt_path)
    runtime_config.validate_errata_workflow_prompts()
    payload = copy.deepcopy(runtime_config.DEFAULT_PROMPT_TEMPLATES["templates"]["errata_workflow"])
    del payload["prompts"]["reviewer"]
    with pytest.raises(ValueError, match="缺少节点提示词"):
        runtime_config.upsert_template("errata_workflow", payload)
    payload = copy.deepcopy(runtime_config.DEFAULT_PROMPT_TEMPLATES["templates"]["errata_workflow"])
    payload["prompts"]["reviewer"]["user"] = "缺少草稿占位符"
    with pytest.raises(ValueError, match="缺少占位符"):
        runtime_config.upsert_template("errata_workflow", payload)


def test_runtime_model_settings_mask_and_preserve_api_key(tmp_path, monkeypatch):
    runtime_path = tmp_path / "runtime_settings.yaml"
    public_defaults_path = tmp_path / "model_defaults.local.yaml"
    private_defaults_path = tmp_path / "model_defaults.local.private.yaml"
    prompt_path = tmp_path / "prompt_templates.yaml"
    monkeypatch.setattr(runtime_config, "RUNTIME_SETTINGS_PATH", runtime_path)
    monkeypatch.setattr(runtime_config, "MODEL_DEFAULTS_PATH", public_defaults_path)
    monkeypatch.setattr(runtime_config, "PRIVATE_MODEL_DEFAULTS_PATH", private_defaults_path)
    monkeypatch.setattr(runtime_config, "PROMPT_TEMPLATES_PATH", prompt_path)

    private_defaults_path.write_text(
        yaml.safe_dump(
            {
                **runtime_config.DEFAULT_MODEL_DEFAULTS,
                "solver_config": {
                    "model_name": "configured-model",
                    "api_key": "sk-secret-value-1234",
                    "base_url": "https://example.test/v1",
                    "max_tokens": 5000,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    response = runtime_config.update_runtime_settings(
        {
            "solver_config": {
                "model_name": "updated-model",
                "api_key": "",
            }
        }
    )
    assert response["solver_config"]["model_name"] == "updated-model"
    assert response["solver_config"]["api_key_configured"] is True
    assert "secret" not in json.dumps(response, ensure_ascii=False)
    assert runtime_config.read_model_defaults()["solver_config"]["api_key"] == "sk-secret-value-1234"
    assert not list(tmp_path.glob("*.tmp"))


def test_runtime_mineru_settings_mask_clear_and_refresh_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(mineru_v4_service, "_mineru_v4_service", None)
    monkeypatch.setattr(mineru_v4_service, "_mineru_v4_service_config", None)

    initial = mineru_v4_service.update_mineru_settings(
        {
            "api_token": "mineru-secret-token-1234",
            "base_url": "https://first.example/v4/",
        }
    )
    assert initial == {
        "base_url": "https://first.example/v4",
        "api_token_masked": "min************1234",
        "api_token_configured": True,
    }
    assert "secret" not in json.dumps(initial)
    first_client = mineru_v4_service.get_mineru_v4_service()

    runtime_path = tmp_path / "runtime_settings.yaml"
    public_defaults_path = tmp_path / "model_defaults.local.yaml"
    private_defaults_path = tmp_path / "model_defaults.local.private.yaml"
    monkeypatch.setattr(runtime_config, "RUNTIME_SETTINGS_PATH", runtime_path)
    monkeypatch.setattr(runtime_config, "MODEL_DEFAULTS_PATH", public_defaults_path)
    monkeypatch.setattr(runtime_config, "PRIVATE_MODEL_DEFAULTS_PATH", private_defaults_path)
    updated = runtime_config.update_runtime_settings(
        {
            "mineru_config": {
                "api_token": "mineru-next-token-5678",
                "base_url": "https://next.example/v4",
            }
        }
    )
    assert updated["mineru_config"]["base_url"] == "https://next.example/v4"
    assert updated["mineru_config"]["api_token_configured"] is True
    RuntimeSettingsResponse.model_validate(updated)
    second_client = mineru_v4_service.get_mineru_v4_service()
    assert second_client is not first_client
    assert second_client.api_token == "mineru-next-token-5678"

    cleared = mineru_v4_service.update_mineru_settings({"clear_api_token": True})
    assert cleared["api_token_configured"] is False
    assert yaml.safe_load((tmp_path / "mineru.local.private.yaml").read_text(encoding="utf-8"))["api_token"] == ""
    asyncio.run(first_client.aclose())
    asyncio.run(second_client.aclose())


def test_admin_delete_cascades_errata_mapping_artifacts_and_logs():
    Base.metadata.create_all(bind=engine)
    ensure_task_preview_columns()
    suffix = uuid.uuid4().hex
    task_id = f"delete_errata_{suffix}"
    job_id = f"delete_job_{suffix}"
    item_id = f"delete_item_{suffix}"
    with SessionLocal() as db:
        db.add(ErrataJob(job_id=job_id, original_filename="delete.docx", source_path="delete.docx"))
        db.add(
            Task(
                task_id=task_id,
                thread_id=task_id,
                image_url="",
                state="manual",
                retry_count=0,
                workflow_type="errata",
                source_kind="errata",
                source_id=job_id,
                source_item_id=item_id,
            )
        )
        db.flush()
        db.add(ErrataItem(item_id=item_id, job_id=job_id, item_index=1, task_id=task_id))
        db.add(TaskArtifact(task_id=task_id, node_name="solver", input_revision=1, content="draft"))
        db.add(AgentLog(task_id=task_id, node_name="solver", request_payload="{}", response_payload="{}"))
        db.commit()

        admin_delete_task(task_id, db)
        assert db.query(Task).filter(Task.task_id == task_id).count() == 0
        assert db.query(ErrataItem).filter(ErrataItem.item_id == item_id).count() == 0
        assert db.query(TaskArtifact).filter(TaskArtifact.task_id == task_id).count() == 0
        assert db.query(AgentLog).filter(AgentLog.task_id == task_id).count() == 0
        db.query(ErrataJob).filter(ErrataJob.job_id == job_id).delete()
        db.commit()


def test_manual_formatter_edit_invalidates_downstream_artifacts():
    Base.metadata.create_all(bind=engine)
    ensure_task_preview_columns()
    suffix = uuid.uuid4().hex
    task_id = f"edit_errata_{suffix}"
    job_id = f"edit_job_{suffix}"
    item_id = f"edit_item_{suffix}"
    with SessionLocal() as db:
        db.add(ErrataJob(job_id=job_id, original_filename="edit.docx", source_path="edit.docx"))
        db.add(Task(task_id=task_id, thread_id=task_id, image_url="", state="completed", retry_count=0, input_revision=1, final_result="旧正文", workflow_type="errata", source_kind="errata", source_id=job_id, source_item_id=item_id))
        db.add(ErrataItem(item_id=item_id, job_id=job_id, item_index=1, task_id=task_id, status="completed", result_type="rewrite", final_text_markup="旧正文"))
        db.add_all([
            TaskArtifact(task_id=task_id, node_name="formatter", input_revision=1, content="旧标准答案"),
            TaskArtifact(task_id=task_id, node_name="errata_adjudication", input_revision=1, content="{}"),
            TaskArtifact(task_id=task_id, node_name="word_composition", input_revision=1, content="旧正文"),
        ])
        db.commit()

        response = update_errata_item(item_id, ErrataItemUpdateRequest(solution_text="人工标准答案"))
        assert response["solution_text"] == "人工标准答案"
        assert response["final_text_markup"] == ""
        db.expire_all()
        task = db.query(Task).filter(Task.task_id == task_id).one()
        item = db.query(ErrataItem).filter(ErrataItem.item_id == item_id).one()
        assert task.input_revision == 2
        assert task.final_result is None
        assert task.state == "manual"
        assert item.result_type is None
        assert db.query(TaskArtifact).filter(TaskArtifact.task_id == task_id, TaskArtifact.node_name == "errata_adjudication", TaskArtifact.input_revision == 2).count() == 0

        db.query(TaskArtifact).filter(TaskArtifact.task_id == task_id).delete()
        db.delete(item)
        db.delete(task)
        db.query(ErrataJob).filter(ErrataJob.job_id == job_id).delete()
        db.commit()


def test_errata_reviewer_receives_only_question_and_solver_draft(monkeypatch):
    Base.metadata.create_all(bind=engine)
    task_id = f"review_isolation_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        db.add(Task(task_id=task_id, thread_id=task_id, image_url="", state="reviewing", retry_count=0, workflow_type="errata"))
        db.commit()

    captured = {}

    class FakeLlm:
        def with_structured_output(self, *args, **kwargs):
            return self

    async def fake_call(**kwargs):
        captured["messages"] = kwargs["messages"]
        return ReviewDecision(is_pass=True, feedback="")

    monkeypatch.setattr(reviewer, "get_llm", lambda config: FakeLlm())
    monkeypatch.setattr(llm_client, "call_with_retry_and_fallback", fake_call)
    monkeypatch.setattr(llm_client, "log_agent_interaction", lambda *args, **kwargs: None)
    state = {
        "task_id": task_id,
        "input_revision": 1,
        "image_url": "",
        "image_urls": ["data:image/png;base64,cXVlc3Rpb24="],
        "status": "reviewing",
        "draft_solution": "独立计算结果为 8 条。",
        "review_decision": None,
        "review_feedback": None,
        "final_result": None,
        "formatted_solution": None,
        "errata_decision": None,
        "retry_count": 0,
        "error_msg": None,
        "failed_node": None,
        "total_tokens": 0,
        "target_nodes": None,
        "agent_configs": {"reviewer": {}},
        "workflow_template_id": "errata_workflow",
        "question_text": "题干只询问支路数量。",
        "workflow_type": "errata",
        "source_id": None,
        "source_item_id": None,
    }
    result = asyncio.run(reviewer.review_node(state))
    prompt_text = json.dumps([message.content for message in captured["messages"]], ensure_ascii=False)
    assert result["review_decision"] == "PASS"
    assert "8 条" in prompt_text
    assert "9 条" not in prompt_text
    assert "原答案" not in prompt_text

    with SessionLocal() as db:
        db.query(AgentLog).filter(AgentLog.task_id == task_id).delete()
        db.query(TaskArtifact).filter(TaskArtifact.task_id == task_id).delete()
        db.query(Task).filter(Task.task_id == task_id).delete()
        db.commit()


def test_full_errata_rerun_clears_stale_workflow_state(monkeypatch):
    Base.metadata.create_all(bind=engine)
    task_id = f"rerun_errata_{uuid.uuid4().hex}"
    stale_history = {
        "workflow_type": "errata",
        "draft_solution": "旧草稿",
        "formatted_solution": "旧标准答案",
        "errata_decision": {"result_type": "correct"},
        "review_decision": "FAIL",
        "review_feedback": "旧反馈",
        "failed_node": "reviewer",
    }
    with SessionLocal() as db:
        db.add(
            Task(
                task_id=task_id,
                thread_id=task_id,
                image_url="",
                state="failed",
                retry_count=1,
                workflow_type="errata",
                history=json.dumps(stale_history, ensure_ascii=False),
            )
        )
        db.commit()

    captured = {}

    async def fake_run(task_id_arg, start_node, target_nodes):
        captured.update(task_id=task_id_arg, start_node=start_node, target_nodes=target_nodes)

    monkeypatch.setattr("app.main.run_agent_workflow_async", fake_run)
    asyncio.run(run_errata_task(task_id))

    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == task_id).one()
        history = json.loads(task.history)
        assert task.retry_count == 0
        assert not ({"draft_solution", "formatted_solution", "errata_decision", "review_decision", "review_feedback", "failed_node"} & history.keys())
        db.delete(task)
        db.commit()
    assert captured["start_node"] == "solver"
