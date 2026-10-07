"""The tools a driver turn offers: ``select_hat`` and the tools the base
package and the chosen hat's package register. The schemas are the agent's
own; which tools exist is the package registry's answer, never a choice
made from the question's words."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SELECT_HAT = "select_hat"


def select_hat_schema(disciplines: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": SELECT_HAT,
            "description": "Wear the hat whose description fits the question. Its tools and "
                           "guidance are added for the rest of the turn.",
            "parameters": {
                "type": "object",
                "properties": {"hat": {"type": "string", "enum": sorted(disciplines)}},
                "required": ["hat"],
            },
        },
    }


def offered(agent: Any, project_id: Optional[str], hat: Optional[str], disciplines: List[str]) -> List[Dict[str, Any]]:
    import dataclasses

    from app.agents.core import tool_registry

    names = set(tool_registry.tools_of("base"))
    if hat:
        names |= set(tool_registry.tools_of(hat))
    # The package tools' schemas are written next to the construction block's
    # in tool_definitions and emitted only when that block is allowed; in
    # driver mode the registry, not the agent's block list, decides.
    schema_source = dataclasses.replace(
        agent, allowed_blocks=sorted(set(agent.allowed_blocks) | {"construction"}))
    tools = [t for t in schema_source.tool_definitions(project_id=project_id)
             if (t.get("function") or {}).get("name") in names]
    # Tools that declare their schema where they are defined (e.g. general
    # knowledge) are offered from the registry.
    have = {(t.get("function") or {}).get("name") for t in tools}
    for name in sorted(names - have):
        spec = tool_registry.get(name)
        if spec is not None and spec.schema:
            tools.append(spec.schema)
    from app.agents.driver import routes

    workflow = routes.schema(routes.catalogue())
    return [select_hat_schema(disciplines)] + tools + ([workflow] if workflow else [])
