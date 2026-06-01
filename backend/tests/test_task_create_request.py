import pytest

from app.main import requires_draft_for_entry_point, validate_requested_target_nodes
from app.models.schemas import TaskCreateRequest


def test_task_create_request_allows_text_only():
    payload = TaskCreateRequest(question_text="题目文本")
    assert payload.question_text == "题目文本"


def test_task_create_request_allows_text_and_images():
    payload = TaskCreateRequest(
        question_text="题目文本",
        image_urls=["data:image/png;base64,abc"],
    )
    assert payload.question_text == "题目文本"
    assert payload.image_urls == ["data:image/png;base64,abc"]


def test_task_create_request_rejects_missing_all_inputs():
    with pytest.raises(ValueError):
        TaskCreateRequest()


def test_validate_requested_target_nodes_allows_solver_to_formatter():
    assert validate_requested_target_nodes(["solver", "formatter"]) == [
        "solver",
        "formatter",
    ]


def test_validate_requested_target_nodes_rejects_out_of_order_nodes():
    assert validate_requested_target_nodes(["formatter", "solver"]) == []


def test_validate_requested_target_nodes_rejects_duplicate_nodes():
    assert validate_requested_target_nodes(["solver", "solver", "formatter"]) == []


def test_requires_draft_for_non_solver_entry_points():
    assert requires_draft_for_entry_point("reviewer", None) is True
    assert requires_draft_for_entry_point("formatter", "") is True
    assert requires_draft_for_entry_point("formatter", "已有草稿") is False
    assert requires_draft_for_entry_point("solver", None) is False
