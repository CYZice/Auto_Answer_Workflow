import importlib.util
import json
from pathlib import Path

import pytest


pytest.importorskip("playwright.async_api")


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "target_system_delivery_worker.py"
SPEC = importlib.util.spec_from_file_location("target_system_delivery_worker", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_worker_requires_calibrated_browser_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "CALIBRATION_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="未找到浏览器校准产物"):
        MODULE.load_contract()

    (tmp_path / "browser-contract.json").write_text(json.dumps({"open_button_selector": "button"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="缺少字段"):
        MODULE.load_contract()

    contract = {
        "open_button_selector": "button:has-text('查看')",
        "recognize_button_text": "识别录入",
        "upload_selector": "input[type=file]",
        "ocr_editor_selector": "[contenteditable=true]",
        "exam_point_selector": "input[name=exam_point]",
    }
    (tmp_path / "browser-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    assert MODULE.load_contract() == contract
