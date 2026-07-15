import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "capture_target_system_calibration.py"
SPEC = importlib.util.spec_from_file_location("target_system_calibration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_calibration_redacts_credentials_without_recording_them():
    payload = {"id": 89611, "answer": "答案", "token": "secret", "nested": {"password": "secret"}}
    assert MODULE.redact(payload) == {"id": 89611, "answer": "答案", "token": "***", "nested": {"password": "***"}}
