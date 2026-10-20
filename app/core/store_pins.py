"""Read the Cerebrum-Blocks Store pin lock.

The lock records one certified block sha per id. ``store_sha`` is null
until ``scripts/bump_store_pin.py`` writes the sha that landed in the
Store. This module does not install blocks and does not invent ids.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "locks" / "store_pins.lock.json"

POSTURE_BLOCK_ID = "posture_model"


def load_lock(path: Path | None = None) -> dict[str, Any]:
    lock_path = path or LOCK_PATH
    with open(lock_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), dict):
        raise ValueError(f"store pin lock {lock_path} has no blocks object")
    return data


def block_pin(block_id: str, path: Path | None = None) -> dict[str, Any]:
    blocks = load_lock(path).get("blocks") or {}
    pin = blocks.get(block_id)
    if not isinstance(pin, dict):
        raise KeyError(block_id)
    return pin


def posture_client_pin(path: Path | None = None) -> dict[str, Any]:
    """Client coordinates for the posture model. Store sha may still be null."""
    pin = block_pin(POSTURE_BLOCK_ID, path)
    client = pin.get("client")
    if not isinstance(client, dict):
        raise ValueError("posture_model pin has no client object")
    return {
        "store_block_id": pin.get("store_block_id") or POSTURE_BLOCK_ID,
        "store_sha": pin.get("store_sha"),
        "status": pin.get("status"),
        "distribution": client.get("distribution"),
        "git_sha": client.get("git_sha"),
        "import_root": client.get("import_root"),
        "serve_config": client.get("serve_config"),
        "system_prompt_key": client.get("system_prompt_key") or "system_prompt_file",
        "checkpoint": client.get("checkpoint"),
    }
