import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.automation.state_machine import validate_transition


def test_automation_state_machine_transitions():
    validate_transition("discovered", "selected")
    validate_transition("selected", "grabbed")
    validate_transition("grabbed", "solving")
    validate_transition("solving", "filled")
    validate_transition("filled", "review_pending")
    validate_transition("review_pending", "ready_to_submit")
    validate_transition("ready_to_submit", "submitting")
    validate_transition("submitting", "submitted")


def test_automation_state_machine_illegal_transition():
    failed = False
    try:
        validate_transition("discovered", "submitted")
    except ValueError:
        failed = True
    assert failed, "非法状态流转必须抛出异常"


if __name__ == "__main__":
    test_automation_state_machine_transitions()
    test_automation_state_machine_illegal_transition()
    print("automation state machine tests passed")
