#!/usr/bin/env python3
"""Bump one existing Store block pin to a certified sha.

Usage:
    python scripts/bump_store_pin.py posture_model <store_sha>

Refuses ids that are not already in locks/store_pins.lock.json.
Does not add blocks. ``retrieval_core`` is not a Store block and
cannot be introduced here. Only ``store_sha`` and ``status`` change.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "locks" / "store_pins.lock.json"

_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_REFUSED_IDS = frozenset({"retrieval_core"})


def bump(block_id: str, store_sha: str, path: Path = LOCK_PATH) -> dict:
    if block_id in _REFUSED_IDS:
        raise SystemExit(
            f"{block_id} is not a Store block; refusing to add it to the lock"
        )
    if not _SHA.fullmatch(store_sha or ""):
        raise SystemExit(f"store sha {store_sha!r} is not a 7-40 hex git sha")
    data = json.loads(path.read_text(encoding="utf-8"))
    blocks = data.get("blocks")
    if not isinstance(blocks, dict) or block_id not in blocks:
        known = sorted(blocks) if isinstance(blocks, dict) else []
        raise SystemExit(
            f"{block_id} is not in the lock (known: {', '.join(known) or 'none'}). "
            "This helper only bumps an existing pin."
        )
    pin = blocks[block_id]
    if not isinstance(pin, dict):
        raise SystemExit(f"{block_id} pin is not an object")
    pin["store_sha"] = store_sha.lower()
    pin["status"] = "pinned"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return pin


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            "usage: python scripts/bump_store_pin.py <block_id> <store_sha>",
            file=sys.stderr,
        )
        return 2
    pin = bump(args[0], args[1])
    print(f"pinned {args[0]} store_sha={pin['store_sha']} status={pin['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
