import asyncio

from app.agent.graph import route_after_solver
from app.agent.nodes import solver as solver_module


def test_route_after_solver_end_on_failed():
    state = {"status": "failed"}
    assert route_after_solver(state) == "end"


def test_route_after_solver_end_on_cancelled():
    state = {"status": "cancelled"}
    assert route_after_solver(state) == "end"


def test_route_after_solver_go_reviewer_when_active():
    state = {"status": "reviewing"}
    assert route_after_solver(state) == "reviewer"


def test_route_after_solver_end_on_unexpected_status():
    state = {"status": "solving"}
    assert route_after_solver(state) == "end"


def test_solve_node_allows_question_text_without_images(monkeypatch):
    async def fake_solve_image(
        image_urls,
        review_feedback,
        model_config,
        workflow_template_id,
        task_id,
        question_text,
    ):
        assert image_urls == []
        assert question_text == "题目文本"
        return {"draft": "解题草稿", "tokens": 12}

    monkeypatch.setattr(solver_module, "solve_node_sync", lambda task_id: False)
    monkeypatch.setattr(solver_module, "solve_image", fake_solve_image)

    state = {
        "task_id": "q_test_text_only",
        "image_url": "",
        "image_urls": [],
        "status": "queued",
        "review_feedback": None,
        "agent_configs": {},
        "workflow_template_id": "workflow_a",
        "question_text": "题目文本",
        "total_tokens": 0,
    }

    result = asyncio.run(solver_module.solve_node(state))
    assert result["status"] == "reviewing"
    assert result["draft_solution"] == "解题草稿"
    assert result["total_tokens"] == 12


def test_solve_node_fails_when_no_images_and_no_question_text(monkeypatch):
    monkeypatch.setattr(solver_module, "solve_node_sync", lambda task_id: False)

    state = {
        "task_id": "q_test_missing_input",
        "image_url": "",
        "image_urls": [],
        "status": "queued",
        "review_feedback": None,
        "agent_configs": {},
        "workflow_template_id": "workflow_a",
        "question_text": "",
        "total_tokens": 0,
    }

    result = asyncio.run(solver_module.solve_node(state))
    assert result["status"] == "failed"
    assert result["failed_node"] == "solver"
    assert "Missing both image_urls and question_text" in result["error_msg"]
