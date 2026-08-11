"""Every public construction action must be dispatchable by route().

Audit 2026-08-12. Three fully-implemented public actions were unreachable —
`generate_construction_report`, `extract_measurements`, `track_progress`. None
was a stub; the router simply had no entry for them, so `route()` answered
"Unknown action" for capabilities that exist and work.

One of them was user-facing: `_recommend_next_action` suggests
"generate_construction_report" by name after a QA/QC run with defects, and
dispatching that suggestion returned an error. The platform recommended
something it then refused to run.

Nothing in the suite caught it because every test dispatches actions it already
knows are wired. This checks the OTHER direction — from the definitions to the
router — which is the only direction that can find a capability nobody routes.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CONTAINER_DIR = Path("app/containers/construction")
INIT = CONTAINER_DIR / "__init__.py"


def _public_actions() -> set[str]:
    """Methods shaped like a container action: (self, input_data, params)."""
    found = set()
    for path in sorted(CONTAINER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                args = [a.arg for a in node.args.args]
                if (len(args) >= 3 and args[0] == "self"
                        and args[1] == "input_data" and args[2] == "params"
                        and not node.name.startswith("_")):
                    found.add(node.name)
    return found


def _routed_methods() -> set[str]:
    """Methods the router's handlers table can actually reach.

    Parsed as `self.<name>`, NOT as dict keys: the table aliases deliberately
    (`"schedule_risk": self.analyze_schedule_risk`), so comparing keys to
    method names reports false positives. That mistake was made once during
    this audit — hence the explicit note.
    """
    src = INIT.read_text(encoding="utf-8")
    start = src.index("handlers = {")
    block = src[start:src.index("\n        }", start)]
    return set(re.findall(r"self\.([a-z0-9_]+)", block))


def test_every_public_action_is_routable():
    unreachable = sorted(_public_actions() - _routed_methods())
    assert not unreachable, (
        "these public actions exist but route() cannot dispatch them, so the "
        "capability is dead to every caller: " + ", ".join(unreachable) +
        "\nAdd them to the handlers table in ConstructionContainer.route(), or "
        "delete them and register the removal in KNOWN_INCOMPLETE.md."
    )


def test_router_does_not_reference_missing_methods():
    """The inverse: a handlers entry pointing at a method that does not exist
    would raise AttributeError at import of the table, not at dispatch."""
    from app.containers.construction import ConstructionContainer

    container = ConstructionContainer()
    for name in _routed_methods():
        assert hasattr(container, name), (
            f"route() maps to self.{name}, which does not exist on the container"
        )


@pytest.mark.asyncio
async def test_recommended_actions_are_dispatchable():
    """Anything the platform SUGGESTS must be something it can then run.

    This is the specific bug that motivated the file: a recommendation naming
    an action the router rejected.
    """
    from app.containers.construction import ConstructionContainer

    src = INIT.read_text(encoding="utf-8")
    suggested = set(re.findall(r'"suggested_action":\s*"([a-z0-9_]+)"', src))
    assert suggested, "no suggested_action literals found — has the shape changed?"

    container = ConstructionContainer()
    for action in sorted(suggested):
        result = await container.route(action, {}, {})
        assert "Unknown action" not in str(result.get("error", "")), (
            f"the platform suggests {action!r} but route() cannot dispatch it: "
            f"{result.get('error')}"
        )
