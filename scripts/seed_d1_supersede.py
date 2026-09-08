#!/usr/bin/env python3
"""Hide the stale D1 letter extract behind the corrected copy.

Sets ``b5033ec2.superseded_by = 93982d45`` and ``retrieval_visible=false``
when both rows exist. No-op otherwise. Never deletes.

Same seed as Alembic 0017 so a late-arriving corrected copy can be wired
after deploy. Reversible: flip ``retrieval_visible`` back to true.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)
    from app.core.projects import seed_d1_letter_supersede

    report = seed_d1_letter_supersede()
    print(json.dumps(report, indent=2))
    return 0 if report.get("applied") or not (
        report.get("stale_present") ^ report.get("live_present")
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
