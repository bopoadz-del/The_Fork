"""PR #78 smoke test — exercise the routing gate against the operator's
two representative queries.

This runs ``select_agent_for_message`` directly (no FastAPI client, no
LLM call) so the result is a clean read of the routing decision the
helper would make for a real chat request.

Reports the routing dict for each query and a PASS/FAIL verdict against
the expected behaviour:

  1. Generative-intent query → re-target to heavy-reasoning
  2. Q&A query → stay on project-assistant (RAG-eligible)

Usage:
    .venv/Scripts/python.exe scripts/smoke_pr78.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env so CEREBRUM_DOMAIN_KITS=construction is set BEFORE we touch
# app.blocks — otherwise the construction kit (which owns the
# smart_orchestrator block) doesn't register and the routing helper
# returns reason="smart_orchestrator_not_registered" on every query.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# Belt-and-suspenders: if .env didn't set it (e.g. running in a stripped
# environment), force construction kit on for the smoke. Smoke tests
# should never silently fall back to a no-routing pass-through.
os.environ.setdefault("CEREBRUM_DOMAIN_KITS", "construction")

from app.agents.runtime import (  # noqa: E402
    Agent,
    AGENT_REGISTRY,
    select_agent_for_message,
)


def _stub(name: str) -> Agent:
    return Agent(
        name=name,
        description=f"{name} smoke stub",
        system_prompt="(smoke stub)",
        allowed_blocks=[],
    )


async def smoke() -> int:
    AGENT_REGISTRY.clear()
    pa = _stub("project-assistant")
    hr = _stub("heavy-reasoning")
    AGENT_REGISTRY["project-assistant"] = pa
    AGENT_REGISTRY["heavy-reasoning"] = hr

    # Operator's two literal queries from the brief.
    queries = [
        {
            "query": "generate a WBS for a 10-floor tower",
            "kind": "generative",
            "expected_final": "heavy-reasoning",
            "expected_reason": "needs_planning",
        },
        {
            "query": "what is the SPI formula",
            # Operator decision 2026-06-19 (PR #80 amendment): "SPI formula"
            # routes to heavy-reasoning via sympy_reason after the gate-2
            # relaxation. Semantically correct — it IS a formula/symbolic-
            # reasoning question. Acceptable behaviour, not a failure.
            "kind": "qa/formula",
            "expected_final": "heavy-reasoning",
            "expected_reason": "needs_planning",
        },
    ]

    # Plus a stronger variant of the generative query so the operator can
    # see what a clean routing match looks like vs the sparse one above.
    queries.append({
        "query": "Create L2 schedule with 200 activities for a 10-floor tower.",
        "kind": "generative (clean keywords)",
        "expected_final": "heavy-reasoning",
        "expected_reason": "needs_planning",
    })

    print("=" * 72)
    print("PR #78 smoke test — routing gate")
    print("=" * 72)
    print()

    fails = 0
    for case in queries:
        final, routing = await select_agent_for_message(case["query"], pa)
        ok_final = routing["final"] == case["expected_final"]
        ok_reason = (
            case["expected_reason"] is None
            or routing["reason"] == case["expected_reason"]
        )
        verdict = "PASS" if (ok_final and ok_reason) else "FAIL"
        if verdict == "FAIL":
            fails += 1
        print(f"[ {verdict} ]  {case['kind']}")
        print(f"          Q: {case['query']!r}")
        print(f"          Expected final: {case['expected_final']}")
        print(f"          Got: final={routing['final']!r} action={routing['action']!r} "
              f"confidence={routing['confidence']:.3f} reason={routing['reason']!r}")
        print()

    print("=" * 72)
    if fails == 0:
        print(f"  Smoke verdict: PASS — {len(queries)} / {len(queries)} cases routed as expected.")
    else:
        print(f"  Smoke verdict: FAIL — {fails} / {len(queries)} cases routed unexpectedly.")
    print("=" * 72)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke()))
