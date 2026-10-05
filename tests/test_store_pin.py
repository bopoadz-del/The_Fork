"""Store pin lock: posture_model is pinned; retrieval_core is absent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.store_pins import LOCK_PATH, posture_client_pin
from scripts.bump_store_pin import bump

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements-posture.txt"


def test_lock_pins_signed_posture_store_sha():
    pin = posture_client_pin()
    assert pin["store_block_id"] == "posture_model"
    assert pin["store_sha"] == "a839c8e513765750dffa068d1dd161f5474d3bca"
    assert pin["status"] == "pinned"
    assert pin["distribution"] == "cerebrum-slm"
    assert pin["git_sha"] == "4e167a7"
    assert pin["import_root"] == "src/posture_model"
    assert pin["serve_config"] == "configs/serve/posture-epoch2.json"
    assert pin["system_prompt_key"] == "system_prompt_file"
    assert pin["checkpoint"].endswith(
        "sampler_weights/posture-qwen3-8b-lora-r16-epoch2"
    )


def test_lock_does_not_invent_retrieval_core():
    data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert "retrieval_core" not in data["blocks"]
    blob = LOCK_PATH.read_text(encoding="utf-8")
    assert "retrieval_core" not in blob


def test_requirements_posture_matches_lock_sha():
    text = REQ.read_text(encoding="utf-8")
    assert "cerebrum-slm.git@4e167a7" in text
    # The default app install must not pull a repo CI cannot fetch.
    main_req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "requirements-posture" not in main_req
    assert "cerebrum-slm" not in main_req


def test_bump_writes_sha_without_adding_blocks(tmp_path: Path):
    src = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "store_pins.lock.json"
    path.write_text(json.dumps(src), encoding="utf-8")
    pin = bump("posture_model", "abc1234", path)
    assert pin["store_sha"] == "abc1234"
    assert pin["status"] == "pinned"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved["blocks"]) == {"posture_model"}
    assert saved["blocks"]["posture_model"]["client"]["git_sha"] == "4e167a7"


def test_bump_refuses_unknown_and_retrieval_core(tmp_path: Path):
    path = tmp_path / "store_pins.lock.json"
    path.write_text(LOCK_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(SystemExit):
        bump("retrieval_core", "abc1234", path)
    with pytest.raises(SystemExit):
        bump("not_a_block", "abc1234", path)
    with pytest.raises(SystemExit):
        bump("posture_model", "not-a-sha", path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["blocks"]["posture_model"]["store_sha"] == (
        "a839c8e513765750dffa068d1dd161f5474d3bca"
    )
