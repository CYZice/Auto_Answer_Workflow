import copy
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from app.services.mineru_v4_service import public_mineru_settings, update_mineru_settings


CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).resolve().parent.parent / "config")))
RUNTIME_SETTINGS_PATH = CONFIG_DIR / "runtime_settings.yaml"
PROMPT_TEMPLATES_PATH = CONFIG_DIR / "prompt_templates.yaml"
MODEL_DEFAULTS_PATH = CONFIG_DIR / "model_defaults.local.yaml"
PRIVATE_MODEL_DEFAULTS_PATH = CONFIG_DIR / "model_defaults.local.private.yaml"

ERRATA_PROMPT_NODES = (
    "solver",
    "reviewer",
    "formatter",
    "errata_adjudication",
    "word_composition",
)
ERRATA_PROMPT_PLACEHOLDERS = {
    "reviewer": "{draft_solution}",
    "formatter": "{draft_solution}",
    "errata_adjudication": "{errata_context}",
    "word_composition": "{formatted_solution}",
}

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
        "errata_workflow": {
            "name": "勘误工作流",
            "description": "独立解题、结构化裁决与确定性 Word 写回",
            "prompts": {
                "solver": {
                    "inherit": "workflow_a.solver",
                    "system": "",
                    "user": "",
                },
                "reviewer": {
                    "system": "你负责审查大学物理、电路分析和模拟电子技术题目的独立解题草稿。只审查明确标注的草稿答案，不得把题目图片中的其他文字当作待审答案。仅输出 is_pass 与 feedback。",
                    "user": "题目见题干证据。待审查草稿：\n\n{draft_solution}",
                },
                "formatter": {
                    "inherit": "workflow_a.formatter",
                    "system": "",
                    "user": "",
                },
                "errata_adjudication": {
                    "system": "你是独立勘误裁决员。分别判断标准答案、题干、原答案和勘误意见，不生成或改写最终正文。question_errata 只在题干本身有误时填写。",
                    "user": "{errata_context}",
                },
                "word_composition": {
                    "system": "确定性选择已通过裁决的标准答案，不调用模型。",
                    "user": "{formatted_solution}",
                },
            },
        },
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

DEFAULT_MODEL_DEFAULTS = {
    "shared_model_config": {
        "api_key": "",
        "base_url": "",
    },
    "solver_config": {
        "model_name": "",
        "api_key": "",
        "base_url": "",
        "max_tokens": 4096,
        "use_responses_api": True,
        "reasoning_effort": "xhigh",
        "store": False,
    },
    "reviewer_config": {
        "model_name": "",
        "api_key": "",
        "base_url": "",
        "max_tokens": 2048,
        "use_responses_api": True,
        "reasoning_effort": "xhigh",
        "store": False,
    },
    "formatter_config": {
        "model_name": "",
        "api_key": "",
        "base_url": "",
        "max_tokens": 1024,
        "use_responses_api": True,
        "reasoning_effort": "xhigh",
        "store": False,
    },
    "workflow_template_id": "workflow_a",
    "draft_solution": None,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


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


def normalize_model_config(value: Any, default_max_tokens: int) -> dict[str, Any]:
    config = value if isinstance(value, dict) else {}
    model_name = str(config.get("model_name") or "").strip()
    api_key = str(config.get("api_key") or "").strip()
    base_url = str(config.get("base_url") or "").strip()
    max_tokens = normalize_positive_int(
        config.get("max_tokens"),
        default_max_tokens,
        minimum=1,
    )
    raw_effort = config.get("reasoning_effort", "xhigh")
    if raw_effort is None or not str(raw_effort).strip():
        effort = None
    else:
        effort = str(raw_effort).strip().lower()
        if effort not in {"minimal", "low", "medium", "high", "xhigh"}:
            effort = "xhigh"
    return {
        "model_name": model_name,
        "api_key": api_key,
        "base_url": base_url,
        "max_tokens": max_tokens,
        "use_responses_api": bool(config.get("use_responses_api", True)),
        "reasoning_effort": effort,
        "store": bool(config.get("store", False)),
    }


def normalize_shared_model_config(value: Any) -> dict[str, str]:
    config = value if isinstance(value, dict) else {}
    return {
        "api_key": str(config.get("api_key") or "").strip(),
        "base_url": str(config.get("base_url") or "").strip(),
    }


def _read_stored_model_defaults() -> dict[str, Any]:
    with _LOCK:
        raw = _safe_read_yaml(
            PRIVATE_MODEL_DEFAULTS_PATH if PRIVATE_MODEL_DEFAULTS_PATH.exists() else MODEL_DEFAULTS_PATH,
            DEFAULT_MODEL_DEFAULTS,
        )

    solver = normalize_model_config(raw.get("solver_config"), 4096)
    reviewer = normalize_model_config(raw.get("reviewer_config"), 2048)
    formatter = normalize_model_config(raw.get("formatter_config"), 1024)
    shared_model_config = normalize_shared_model_config(raw.get("shared_model_config"))
    workflow_template_id = str(
        raw.get("workflow_template_id")
        or DEFAULT_MODEL_DEFAULTS["workflow_template_id"]
    ).strip()

    return {
        "solver_config": solver,
        "reviewer_config": reviewer,
        "formatter_config": formatter,
        "shared_model_config": shared_model_config,
        "workflow_template_id": workflow_template_id,
        "draft_solution": raw.get("draft_solution"),
    }


def read_model_defaults() -> dict[str, Any]:
    """Return executable configs with node credentials inheriting shared then env."""
    stored = _read_stored_model_defaults()
    shared = stored["shared_model_config"]
    resolved: dict[str, Any] = {
        "shared_model_config": shared,
        "workflow_template_id": stored["workflow_template_id"],
        "draft_solution": stored["draft_solution"],
    }
    for node_name in ("solver", "reviewer", "formatter"):
        key = f"{node_name}_config"
        config = dict(stored[key])
        config["api_key"] = config["api_key"] or shared["api_key"] or os.getenv("LLM_API_KEY", "")
        config["base_url"] = config["base_url"] or shared["base_url"] or os.getenv("LLM_BASE_URL", "")
        resolved[key] = config
    return resolved


def _mask_api_key(value: str) -> str:
    key = (value or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}{'*' * min(12, len(key) - 7)}{key[-4:]}"


def public_model_defaults() -> dict[str, Any]:
    defaults = _read_stored_model_defaults()
    response: dict[str, Any] = {}
    for node_name in ("solver", "reviewer", "formatter"):
        key = f"{node_name}_config"
        config = defaults[key]
        api_key = config.get("api_key") or ""
        response[key] = {
            "model_name": config.get("model_name") or "",
            "base_url": config.get("base_url") or "",
            "max_tokens": config.get("max_tokens"),
            "api_key_masked": _mask_api_key(api_key),
            "api_key_configured": bool(api_key),
            "use_responses_api": bool(config.get("use_responses_api", True)),
            "reasoning_effort": config.get("reasoning_effort"),
            "store": bool(config.get("store", False)),
        }
    shared = defaults["shared_model_config"]
    response["shared_model_config"] = {
        "base_url": shared.get("base_url") or "",
        "api_key_masked": _mask_api_key(shared.get("api_key") or ""),
        "api_key_configured": bool(shared.get("api_key")),
    }
    return response


def update_model_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    current = _read_stored_model_defaults()
    updated = copy.deepcopy(current)
    shared_payload = payload.get("shared_model_config")
    if isinstance(shared_payload, dict):
        shared = dict(current["shared_model_config"])
        if "base_url" in shared_payload and shared_payload["base_url"] is not None:
            shared["base_url"] = str(shared_payload["base_url"]).strip()
        if shared_payload.get("clear_api_key") is True:
            shared["api_key"] = ""
        elif str(shared_payload.get("api_key") or "").strip():
            shared["api_key"] = str(shared_payload["api_key"]).strip()
        updated["shared_model_config"] = normalize_shared_model_config(shared)
    for node_name, default_tokens in (
        ("solver", 4096),
        ("reviewer", 2048),
        ("formatter", 1024),
    ):
        key = f"{node_name}_config"
        node_payload = payload.get(key)
        if not isinstance(node_payload, dict):
            continue
        next_config = dict(current[key])
        for field in ("model_name", "base_url"):
            if field in node_payload and node_payload[field] is not None:
                next_config[field] = str(node_payload[field]).strip()
        if node_payload.get("max_tokens") is not None:
            next_config["max_tokens"] = normalize_positive_int(
                node_payload["max_tokens"], default_tokens, minimum=1
            )
        for field in ("use_responses_api", "store"):
            if node_payload.get(field) is not None:
                next_config[field] = bool(node_payload[field])
        if node_payload.get("clear_reasoning_effort") is True:
            next_config["reasoning_effort"] = None
        elif node_payload.get("reasoning_effort") is not None:
            next_config["reasoning_effort"] = str(node_payload["reasoning_effort"])
        if node_payload.get("clear_api_key") is True:
            next_config["api_key"] = ""
        elif str(node_payload.get("api_key") or "").strip():
            next_config["api_key"] = str(node_payload["api_key"]).strip()
        updated[key] = normalize_model_config(next_config, default_tokens)
    updated["workflow_template_id"] = str(
        payload.get("active_template_id")
        or current.get("workflow_template_id")
        or "workflow_a"
    ).strip()
    with _LOCK:
        _safe_write_yaml(PRIVATE_MODEL_DEFAULTS_PATH, updated)
    return public_model_defaults()


def read_public_runtime_settings() -> dict[str, Any]:
    return {
        **read_runtime_settings(),
        **public_model_defaults(),
        "mineru_config": public_mineru_settings(),
    }


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
    update_model_defaults({**payload, "active_template_id": active_template_id})
    mineru_payload = payload.get("mineru_config")
    if isinstance(mineru_payload, dict):
        update_mineru_settings(mineru_payload)
    return read_public_runtime_settings()


def read_prompt_templates() -> dict[str, Any]:
    with _LOCK:
        _ensure_file(PROMPT_TEMPLATES_PATH, DEFAULT_PROMPT_TEMPLATES)
        try:
            raw = yaml.safe_load(PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"提示词配置无法解析：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("提示词配置必须是 YAML 对象")
    templates = raw.get("templates") if isinstance(raw.get("templates"), dict) else {}
    return {"templates": templates}


def list_templates() -> list[dict[str, str]]:
    templates = read_prompt_templates().get("templates", {})
    items: list[dict[str, str]] = []
    for template_id, data in templates.items():
        if template_id == "errata_workflow":
            continue
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


def _get_template_exact(template_id: str) -> dict[str, Any] | None:
    data = read_prompt_templates().get("templates", {}).get(template_id)
    return {"template_id": template_id, **data} if isinstance(data, dict) else None


def upsert_template(template_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    template_id = template_id.strip()
    if not template_id:
        raise ValueError("template_id 不能为空")

    name = str(payload.get("name") or template_id).strip() or template_id
    description = str(payload.get("description") or "").strip()
    prompts = payload.get("prompts") if isinstance(payload.get("prompts"), dict) else {}
    if template_id == "errata_workflow":
        missing_nodes = [node for node in ERRATA_PROMPT_NODES if not isinstance(prompts.get(node), dict)]
        if missing_nodes:
            raise ValueError(f"errata_workflow 缺少节点提示词：{', '.join(missing_nodes)}")

    node_names = ERRATA_PROMPT_NODES if template_id == "errata_workflow" else (
        "solver",
        "reviewer",
        "formatter",
    )
    normalized_prompts: dict[str, dict[str, str]] = {}
    for node_name in node_names:
        node_prompt = (
            prompts.get(node_name) if isinstance(prompts.get(node_name), dict) else {}
        )
        normalized_prompt = {
            "system": str(node_prompt.get("system") or "").strip(),
            "user": str(node_prompt.get("user") or "").strip(),
        }
        inherit = str(node_prompt.get("inherit") or "").strip()
        if inherit:
            normalized_prompt["inherit"] = inherit
        normalized_prompts[node_name] = normalized_prompt

    payload_to_save = {
        "name": name,
        "description": description,
        "prompts": normalized_prompts,
    }

    if template_id == "errata_workflow":
        validate_errata_workflow_prompts(
            {"template_id": template_id, **payload_to_save}
        )

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
    template = (
        _get_template_exact(template_id)
        if template_id == "errata_workflow"
        else get_template(template_id)
    )
    if not template:
        if template_id == "errata_workflow":
            raise ValueError("errata_workflow 提示词模板不存在")
        return {"system": "", "user": ""}
    prompts = (
        template.get("prompts") if isinstance(template.get("prompts"), dict) else {}
    )
    node_prompt = (
        prompts.get(node_name) if isinstance(prompts.get(node_name), dict) else {}
    )
    if template_id == "errata_workflow" and not node_prompt:
        raise ValueError(f"errata_workflow 缺少节点提示词：{node_name}")
    seen: set[str] = set()
    while str(node_prompt.get("inherit") or "").strip():
        inherit = str(node_prompt["inherit"]).strip()
        if inherit in seen:
            raise ValueError(f"提示词继承存在循环：{inherit}")
        seen.add(inherit)
        inherited_template_id, separator, inherited_node = inherit.partition(".")
        inherited_template = _get_template_exact(inherited_template_id) if separator else None
        inherited_prompts = (
            inherited_template.get("prompts")
            if isinstance(inherited_template, dict)
            and isinstance(inherited_template.get("prompts"), dict)
            else {}
        )
        inherited_prompt = inherited_prompts.get(inherited_node)
        if not isinstance(inherited_prompt, dict):
            raise ValueError(f"提示词继承目标不存在：{inherit}")
        node_prompt = inherited_prompt
    return {
        "system": str(node_prompt.get("system") or ""),
        "user": str(node_prompt.get("user") or ""),
    }


def validate_errata_workflow_prompts(template: dict[str, Any] | None = None) -> None:
    template = template or _get_template_exact("errata_workflow")
    prompts = template.get("prompts") if isinstance(template, dict) else None
    if not isinstance(prompts, dict):
        raise ValueError("errata_workflow 提示词模板不存在")
    missing_nodes = [node for node in ERRATA_PROMPT_NODES if not isinstance(prompts.get(node), dict)]
    if missing_nodes:
        raise ValueError(f"errata_workflow 缺少节点提示词：{', '.join(missing_nodes)}")
    for node in ERRATA_PROMPT_NODES:
        node_prompt = prompts[node]
        inherit = str(node_prompt.get("inherit") or "").strip()
        if inherit:
            inherited_template_id, separator, inherited_node = inherit.partition(".")
            inherited_template = _get_template_exact(inherited_template_id) if separator else None
            inherited_prompts = inherited_template.get("prompts", {}) if inherited_template else {}
            node_prompt = inherited_prompts.get(inherited_node, {})
        bundle = {
            "system": str(node_prompt.get("system") or ""),
            "user": str(node_prompt.get("user") or ""),
        }
        if not bundle["system"].strip() or not bundle["user"].strip():
            raise ValueError(f"errata_workflow.{node} 的 system/user 不能为空")
        placeholder = ERRATA_PROMPT_PLACEHOLDERS.get(node)
        if placeholder and placeholder not in bundle["user"]:
            raise ValueError(f"errata_workflow.{node}.user 缺少占位符 {placeholder}")


def render_user_prompt(template: str, values: dict[str, Any]) -> str:
    rendered = template or ""
    for key, value in values.items():
        rendered = rendered.replace(
            "{" + key + "}", "" if value is None else str(value)
        )
    return rendered
