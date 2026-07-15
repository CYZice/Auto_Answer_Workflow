import asyncio
import json
import uuid

import yaml

from app.core.database import Base, SessionLocal, engine
from app.main import admin_delete_task, ensure_task_preview_columns
from app.models.domain import AgentLog, ErrataItem, ErrataJob, Task, TaskArtifact
from app.models.schemas import RuntimeSettingsResponse
from app.services import runtime_config
from app.services import mineru_v4_service


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
