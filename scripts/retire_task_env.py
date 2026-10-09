#!/usr/bin/env python3
"""Strip retired environment / secret NAMES from an ECS task definition.

``infra/retired_task_env.json`` lists the names the web task must no longer
carry (for example a secret whose value the code stopped reading). On deploy,
``deploy-aws.yml`` saves the live task definition
(``aws ecs describe-task-definition``) to a file and runs this script on it.
When any container carries a listed name in ``environment`` or ``secrets``,
the script writes a register-ready copy -- identical except for those entries
and the read-only fields ``register-task-definition`` refuses -- and the
workflow registers it and rolls the service onto it. When nothing is listed,
no output file is written and the normal roll runs.

Prints NAMES only, never a value. Stdlib only.

    python scripts/retire_task_env.py --retired infra/retired_task_env.json \\
        --taskdef td.json --out td-new.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Fields describe-task-definition returns that register-task-definition rejects.
READ_ONLY_FIELDS = (
    "taskDefinitionArn",
    "revision",
    "status",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
    "deregisteredAt",
)


def retired_names(spec: Dict[str, Any]) -> List[str]:
    """The names listed in a retired-env data file."""
    out: List[str] = []
    for entry in spec.get("retired") or []:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def retire(taskdef: Dict[str, Any], names: Iterable[str]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Return ``(register-ready task definition, removed names)``.

    ``taskdef`` is describe-task-definition output, with or without its
    ``taskDefinition`` wrapper. Returns ``(None, [])`` when no container
    carries a retired name -- nothing to register. The input is not mutated.
    """
    retired = set(names)
    td = copy.deepcopy(taskdef.get("taskDefinition", taskdef))
    removed: List[str] = []
    for container in td.get("containerDefinitions") or []:
        for key in ("environment", "secrets"):
            entries = container.get(key)
            if not entries:
                continue
            kept = [e for e in entries if e.get("name") not in retired]
            removed.extend(e.get("name") for e in entries if e.get("name") in retired)
            container[key] = kept
    if not removed:
        return None, []
    for field in READ_ONLY_FIELDS:
        td.pop(field, None)
    return td, sorted(set(removed))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--retired", required=True, help="retired-env data file")
    ap.add_argument("--taskdef", required=True, help="describe-task-definition JSON")
    ap.add_argument("--out", required=True, help="where to write the new task definition")
    args = ap.parse_args(argv)

    with open(args.retired, encoding="utf-8") as fh:
        names = retired_names(json.load(fh))
    with open(args.taskdef, encoding="utf-8") as fh:
        taskdef = json.load(fh)
    new_td, removed = retire(taskdef, names)
    if new_td is None:
        print("retire_task_env: nothing to retire")
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(new_td, fh)
    print("retire_task_env: removing " + ", ".join(removed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
