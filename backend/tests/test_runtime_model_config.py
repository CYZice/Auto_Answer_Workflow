from pathlib import Path

from langchain_core.messages import HumanMessage

from app.agent.nodes.llm_client import extract_response_text, get_llm
from app.services import runtime_config


def _isolate_model_defaults(monkeypatch, tmp_path: Path):
    public_path = tmp_path / "model_defaults.local.yaml"
    private_path = tmp_path / "model_defaults.local.private.yaml"
    monkeypatch.setattr(runtime_config, "MODEL_DEFAULTS_PATH", public_path)
    monkeypatch.setattr(runtime_config, "PRIVATE_MODEL_DEFAULTS_PATH", private_path)


def test_node_credentials_inherit_shared_config(monkeypatch, tmp_path):
    _isolate_model_defaults(monkeypatch, tmp_path)

    runtime_config.update_model_defaults(
        {
            "shared_model_config": {
                "base_url": "https://proxy.example/v1",
                "api_key": "shared-secret",
            },
            "solver_config": {"model_name": "gpt-5.6-sol"},
            "formatter_config": {
                "base_url": "https://formatter.example/v1",
                "api_key": "formatter-secret",
            },
        }
    )

    defaults = runtime_config.read_model_defaults()
    assert defaults["solver_config"]["base_url"] == "https://proxy.example/v1"
    assert defaults["solver_config"]["api_key"] == "shared-secret"
    assert defaults["formatter_config"]["base_url"] == "https://formatter.example/v1"
    assert defaults["formatter_config"]["api_key"] == "formatter-secret"

    public = runtime_config.public_model_defaults()
    assert public["shared_model_config"]["api_key_configured"] is True
    assert "shared-secret" not in str(public)
    assert public["solver_config"]["use_responses_api"] is True
    assert public["solver_config"]["reasoning_effort"] == "xhigh"
    assert public["solver_config"]["store"] is False


def test_llm_payload_switches_between_responses_and_chat():
    common = {
        "model_name": "gpt-5.6-sol",
        "api_key": "test-key",
        "base_url": "https://token.yuanxuai.xyz",
        "max_tokens": 512,
    }
    responses_llm = get_llm(
        {
            **common,
            "use_responses_api": True,
            "reasoning_effort": "xhigh",
            "store": False,
        }
    )
    responses_payload = responses_llm._get_request_payload([HumanMessage(content="ping")])
    assert "input" in responses_payload
    assert "messages" not in responses_payload
    assert responses_payload["reasoning"] == {"effort": "xhigh"}
    assert responses_payload["store"] is False
    assert "frequency_penalty" not in responses_payload
    assert responses_llm.default_headers["User-Agent"] == "Yo/JS 4.91.1"

    chat_llm = get_llm(
        {
            **common,
            "use_responses_api": False,
            "reasoning_effort": "xhigh",
        }
    )
    chat_payload = chat_llm._get_request_payload([HumanMessage(content="ping")])
    assert "messages" in chat_payload
    assert "input" not in chat_payload
    assert "reasoning" not in chat_payload
    assert "store" not in chat_payload
    assert chat_llm.default_headers is None


def test_responses_can_omit_reasoning_effort():
    llm = get_llm(
        {
            "model_name": "gpt-5.6-sol",
            "api_key": "test-key",
            "base_url": "https://example.test",
            "max_tokens": 64,
            "use_responses_api": True,
            "reasoning_effort": None,
            "store": False,
        }
    )

    payload = llm._get_request_payload([HumanMessage(content="ping")])
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["store"] is False
    assert "reasoning" not in payload


def test_reasoning_effort_can_be_cleared(monkeypatch, tmp_path):
    _isolate_model_defaults(monkeypatch, tmp_path)
    runtime_config.update_model_defaults(
        {"solver_config": {"reasoning_effort": "xhigh"}}
    )
    runtime_config.update_model_defaults(
        {"solver_config": {"clear_reasoning_effort": True}}
    )

    defaults = runtime_config.read_model_defaults()
    public = runtime_config.public_model_defaults()
    assert defaults["solver_config"]["reasoning_effort"] is None
    assert public["solver_config"]["reasoning_effort"] is None


def test_extract_response_text_supports_responses_api_content_items():
    content = [
        {"type": "reasoning", "summary": [], "content": []},
        {
            "type": "message",
            "content": [
                {"type": "output_text", "text": "第一段"},
                {"type": "output_text", "text": "第二段"},
            ],
        },
    ]

    assert extract_response_text(content) == "第一段\n第二段"
