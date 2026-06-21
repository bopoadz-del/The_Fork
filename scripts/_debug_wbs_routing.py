"""Per-keyword score breakdown for the failing smoke query.

Walks the EXACT keyword list `_match_actions` sees at runtime (including
PROCEDURE_ROUTING_ADDITIONS prepended and the 1.1.0 dedupe merge) and
shows which keywords matched, what each contributed, and the running
total per action. Diagnostic output only — no fixes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
os.environ.setdefault("CEREBRUM_DOMAIN_KITS", "construction")

from app.blocks.smart_orchestrator import (  # noqa: E402
    ACTION_PATTERNS,
    SmartOrchestratorBlock,
    _matches_keyword,
)

QUERY = "generate a WBS for a 10-floor tower"


def main() -> int:
    print(f"Query: {QUERY!r}")
    print(f"Confidence threshold (gate 1 — _match_actions): 0.3")
    print(f"Routing threshold (gate 2 — needs_planning):   0.4")
    print()

    # Mirror _match_actions's 1.1.0 merged-keyword build so we see exactly
    # what the matcher sees.
    merged: dict[str, list[str]] = {}
    for action, kws in ACTION_PATTERNS:
        existing = merged.setdefault(action, [])
        for kw in kws:
            if kw not in existing:
                existing.append(kw)

    hits_per_action: dict[str, list[tuple[str, float]]] = {}
    for action, kws in merged.items():
        for kw in kws:
            if _matches_keyword(kw, QUERY):
                weight = len(kw.split()) * 0.2
                hits_per_action.setdefault(action, []).append((kw, weight))

    if not hits_per_action:
        print("No keywords matched in ANY action's list.")
        print("All scores = 0.0. matched_actions = []. needs_planning = False.")
        return 0

    print("Per-action keyword hits (kw -> score contribution):")
    print()
    rows = []
    for action, hits in hits_per_action.items():
        total = sum(w for _, w in hits)
        rows.append((action, total, hits))
    rows.sort(key=lambda r: -r[1])

    for action, total, hits in rows:
        passes_gate1 = total >= 0.3
        passes_gate2 = total >= 0.4
        gate_str = (
            "above both gates (routes)" if passes_gate2
            else "above gate 1 only (shown in matched_actions, doesn't route)"
            if passes_gate1
            else "BELOW gate 1 (filtered out, never seen by caller)"
        )
        print(f"  action={action!r}  total={total:.3f}  -- {gate_str}")
        for kw, w in hits:
            print(f"      + {kw!r}  ({len(kw.split())} words)  -> {w:.2f}")
        print()

    # Run through the real block so the output matches what the
    # routing helper consumes.
    import asyncio
    block = SmartOrchestratorBlock()
    result = asyncio.get_event_loop().run_until_complete(
        block.process({"user_message": QUERY})
    )
    print("smart_orchestrator.process output:")
    print(f"  matched_actions = {result.get('matched_actions')}")
    print(f"  action_queue    = {result.get('action_queue')}")
    print(f"  fallback_used  -> {bool(result.get('action_queue') and result['action_queue'][0] == 'intelligent_workflow' and not result.get('matched_actions'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
