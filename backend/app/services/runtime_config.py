import copy
import threading
from pathlib import Path
from typing import Any

import yaml


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
RUNTIME_SETTINGS_PATH = CONFIG_DIR / "runtime_settings.yaml"
PROMPT_TEMPLATES_PATH = CONFIG_DIR / "prompt_templates.yaml"

DEFAULT_RUNTIME_SETTINGS = {
    "active_template_id": "workflow_a",
    "request_timeout_seconds": 300,
    "max_retries": 2,
    "fallback": {
        "global": ["gpt-5.4-medium", "gemini-3-flash-preview"],
        "nodes": {
            "solver": [],
            "reviewer": [],
            "formatter": [],
        },
    },
}

DEFAULT_PROMPT_TEMPLATES = {
    "templates": {
        "workflow_a": {
            "name": "默认工作流 A",
            "description": "现网默认解题流程提示词",
            "prompts": {
                "solver": {
                    "system": "你是一位专业解题助手，请给出完整且严谨的推理过程，并使用 LaTeX 表达公式。",
                    "user": "请解析以下图片中的题目。",
                },
                "reviewer": {
                    "system": "你是严格审查员，请独立复算并仅输出结构化字段 is_pass 与 feedback。",
                    "user": "题目：见图片\n答案：\n{draft_solution}",
                },
                "formatter": {
                    "system": "你是排版助手，请将草稿整理为结构清晰的 Markdown 并保留公式。",
                    "user": "请对以下解题草稿进行最终排版：\n\n{draft_solution}",
                },
            },
        }
    }
}

_LOCK = threading.RLock()


def _ensure_file(path: Path, default_value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            yaml.safe_dump(default_value, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


def _safe_read_yaml(path: Path, default_value: dict[str, Any]) -> dict[str, Any]:
    _ensure_file(path, default_value)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return copy.deepcopy(default_value)


def _safe_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    _ensure_file(path, payload)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def normalize_fallback_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        model = item.strip()
        if model and model not in normalized:
            normalized.append(model)
    return normalized


def normalize_positive_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        normalized = int(value)
    except Exception:
        return default
    if normalized < minimum:
        return default
    return normalized


def read_runtime_settings() -> dict[str, Any]:
    with _LOCK:
        raw = _safe_read_yaml(RUNTIME_SETTINGS_PATH, DEFAULT_RUNTIME_SETTINGS)

    active_template_id = str(
        raw.get("active_template_id") or DEFAULT_RUNTIME_SETTINGS["active_template_id"]
    ).strip()
    fallback = raw.get("fallback") if isinstance(raw.get("fallback"), dict) else {}
    fallback_global = normalize_fallback_list(fallback.get("global"))
    fallback_nodes = (
        fallback.get("nodes") if isinstance(fallback.get("nodes"), dict) else {}
    )
    request_timeout_seconds = normalize_positive_int(
        raw.get("request_timeout_seconds"),
        DEFAULT_RUNTIME_SETTINGS["request_timeout_seconds"],
        minimum=1,
    )
    max_retries = normalize_positive_int(
        raw.get("max_retries"),
        DEFAULT_RUNTIME_SETTINGS["max_retries"],
        minimum=0,
    )

    return {
        "active_template_id": active_template_id,
        "request_timeout_seconds": request_timeout_seconds,
        "max_retries": max_retries,
        "fallback": {
            "global": fallback_global,
            "nodes": {
                "solver": normalize_fallback_list(fallback_nodes.get("solver")),
                "reviewer": normalize_fallback_list(fallback_nodes.get("reviewer")),
                "formatter": normalize_fallback_list(fallback_nodes.get("formatter")),
            },
        },
    }


def update_runtime_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = read_runtime_settings()
    active_template_id = str(
        payload.get("active_template_id")
        or current.get("active_template_id")
        or "workflow_a"
    ).strip()

    fallback_payload = (
        payload.get("fallback") if isinstance(payload.get("fallback"), dict) else {}
    )
    current_fallback = (
        current.get("fallback") if isinstance(current.get("fallback"), dict) else {}
    )

    global_models = normalize_fallback_list(
        fallback_payload.get("global")
        if "global" in fallback_payload
        else current_fallback.get("global")
    )

    node_payload = (
        fallback_payload.get("nodes")
        if isinstance(fallback_payload.get("nodes"), dict)
        else {}
    )
    current_nodes = (
        current_fallback.get("nodes")
        if isinstance(current_fallback.get("nodes"), dict)
        else {}
    )
    request_timeout_seconds = normalize_positive_int(
        payload.get("request_timeout_seconds", current.get("request_timeout_seconds")),
        DEFAULT_RUNTIME_SETTINGS["request_timeout_seconds"],
        minimum=1,
    )
    max_retries = normalize_positive_int(
        payload.get("max_retries", current.get("max_retries")),
        DEFAULT_RUNTIME_SETTINGS["max_retries"],
        minimum=0,
    )

    normalized = {
        "active_template_id": active_template_id,
        "request_timeout_seconds": request_timeout_seconds,
        "max_retries": max_retries,
        "fallback": {
            "global": global_models,
            "nodes": {
                "solver": normalize_fallback_list(
                    node_payload.get("solver")
                    if "solver" in node_payload
                    else current_nodes.get("solver")
                ),
                "reviewer": normalize_fallback_list(
                    node_payload.get("reviewer")
                    if "reviewer" in node_payload
                    else current_nodes.get("reviewer")
                ),
                "formatter": normalize_fallback_list(
                    node_payload.get("formatter")
                    if "formatter" in node_payload
                    else current_nodes.get("formatter")
                ),
            },
        },
    }

    with _LOCK:
        _safe_write_yaml(RUNTIME_SETTINGS_PATH, normalized)
    return normalized


def read_prompt_templates() -> dict[str, Any]:
    with _LOCK:
        raw = _safe_read_yaml(PROMPT_TEMPLATES_PATH, DEFAULT_PROMPT_TEMPLATES)
    templates = raw.get("templates") if isinstance(raw.get("templates"), dict) else {}
    return {"templates": templates}


def list_templates() -> list[dict[str, str]]:
    templates = read_prompt_templates().get("templates", {})
    items: list[dict[str, str]] = []
    for template_id, data in templates.items():
        if not isinstance(data, dict):
            continue
        items.append(
            {
                "template_id": str(template_id),
                "name": str(data.get("name") or template_id),
                "description": str(data.get("description") or ""),
            }
        )
    return items


def get_template(template_id: str | None) -> dict[str, Any] | None:
    templates = read_prompt_templates().get("templates", {})
    if template_id and template_id in templates:
        data = templates.get(template_id)
        if isinstance(data, dict):
            return {"template_id": template_id, **data}
    settings = read_runtime_settings()
    active_template_id = settings.get("active_template_id")
    if active_template_id in templates and isinstance(
        templates.get(active_template_id), dict
    ):
        return {"template_id": active_template_id, **templates[active_template_id]}
    if templates:
        first_key = next(iter(templates.keys()))
        first_value = templates[first_key]
        if isinstance(first_value, dict):
            return {"template_id": first_key, **first_value}
    return None


def upsert_template(template_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    template_id = template_id.strip()
    if not template_id:
        raise ValueError("template_id 不能为空")

    name = str(payload.get("name") or template_id).strip() or template_id
    description = str(payload.get("description") or "").strip()
    prompts = payload.get("prompts") if isinstance(payload.get("prompts"), dict) else {}

    normalized_prompts: dict[str, dict[str, str]] = {}
    for node_name in ["solver", "reviewer", "formatter"]:
        node_prompt = (
            prompts.get(node_name) if isinstance(prompts.get(node_name), dict) else {}
        )
        normalized_prompts[node_name] = {
            "system": str(node_prompt.get("system") or "").strip(),
            "user": str(node_prompt.get("user") or "").strip(),
        }

    payload_to_save = {
        "name": name,
        "description": description,
        "prompts": normalized_prompts,
    }

    with _LOCK:
        raw = _safe_read_yaml(PROMPT_TEMPLATES_PATH, DEFAULT_PROMPT_TEMPLATES)
        templates = (
            raw.get("templates") if isinstance(raw.get("templates"), dict) else {}
        )
        templates[template_id] = payload_to_save
        raw["templates"] = templates
        _safe_write_yaml(PROMPT_TEMPLATES_PATH, raw)

    return {"template_id": template_id, **payload_to_save}


def create_template_from(
    source_template_id: str | None,
    new_template_id: str,
    name: str,
    description: str = "",
) -> dict[str, Any]:
    new_template_id = new_template_id.strip()
    if not new_template_id:
        raise ValueError("new_template_id 不能为空")

    templates_data = read_prompt_templates().get("templates", {})
    if new_template_id in templates_data:
        raise ValueError("模板 ID 已存在")

    source = None
    if source_template_id:
        source = templates_data.get(source_template_id)
    if not isinstance(source, dict):
        source = get_template(None)

    source_prompts = source.get("prompts") if isinstance(source, dict) else {}

    payload = {
        "name": (name or new_template_id).strip() or new_template_id,
        "description": (description or "").strip(),
        "prompts": (
            copy.deepcopy(source_prompts)
            if isinstance(source_prompts, dict)
            else {
                "solver": {"system": "", "user": ""},
                "reviewer": {"system": "", "user": ""},
                "formatter": {"system": "", "user": ""},
            }
        ),
    }

    return upsert_template(new_template_id, payload)


def resolve_fallback_models(
    node_name: str, model_config: dict[str, Any] | None = None
) -> list[str]:
    direct = normalize_fallback_list((model_config or {}).get("fallback_models"))
    if direct:
        return direct

    settings = read_runtime_settings()
    fallback = (
        settings.get("fallback") if isinstance(settings.get("fallback"), dict) else {}
    )
    nodes = fallback.get("nodes") if isinstance(fallback.get("nodes"), dict) else {}
    node_specific = normalize_fallback_list(nodes.get(node_name))
    if node_specific:
        return node_specific
    return normalize_fallback_list(fallback.get("global"))


def get_prompt_bundle(node_name: str, template_id: str | None = None) -> dict[str, str]:
    template = get_template(template_id)
    if not template:
        return {"system": "", "user": ""}
    prompts = (
        template.get("prompts") if isinstance(template.get("prompts"), dict) else {}
    )
    node_prompt = (
        prompts.get(node_name) if isinstance(prompts.get(node_name), dict) else {}
    )
    return {
        "system": str(node_prompt.get("system") or ""),
        "user": str(node_prompt.get("user") or ""),
    }


def render_user_prompt(template: str, values: dict[str, Any]) -> str:
    rendered = template or ""
    for key, value in values.items():
        rendered = rendered.replace(
            "{" + key + "}", "" if value is None else str(value)
        )
    return rendered
