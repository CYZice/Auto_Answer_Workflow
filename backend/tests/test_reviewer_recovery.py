import asyncio

from app.agent.nodes import reviewer as reviewer_module


class _FakeResponse:
    def __init__(self, content, total_tokens=0):
        self.content = content
        self.response_metadata = {"token_usage": {"total_tokens": total_tokens}}


class _FakeLLM:
    async def ainvoke(self, messages):
        return _FakeResponse(
            '<think>internal reasoning</think>{"feedback":"","is_pass":true}',
            total_tokens=7,
        )


def test_review_node_recovers_from_raw_json_after_structured_parse_failure(
    monkeypatch,
):
    async def fake_call_with_retry_and_fallback(**kwargs):
        raise RuntimeError(
            "All fallback models and retries failed. Last error: "
            "1 validation error for ReviewDecision Invalid JSON: expected value "
            "at line 1 column 1"
        )

    monkeypatch.setattr(reviewer_module, "review_node_sync", lambda task_id: False)
    monkeypatch.setattr(reviewer_module, "get_llm", lambda config=None: _FakeLLM())
    monkeypatch.setattr(
        "app.agent.nodes.llm_client.call_with_retry_and_fallback",
        fake_call_with_retry_and_fallback,
    )
    monkeypatch.setattr(
        "app.agent.nodes.llm_client.log_agent_interaction", lambda *args, **kwargs: None
    )

    state = {
        "task_id": "reviewer_recovery_case",
        "draft_solution": "draft",
        "agent_configs": {"reviewer": {"model_name": "dummy", "api_key": "x", "base_url": "http://example.com"}},
        "workflow_template_id": "workflow_a",
        "total_tokens": 3,
    }

    result = asyncio.run(reviewer_module.review_node(state))
    assert result["review_decision"] == "PASS"
    assert result["review_feedback"] is None
    assert result["total_tokens"] == 10
