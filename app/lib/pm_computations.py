"""Reusable project-management computations — Reasoning Engine Plan 1.

Pure functions, no AI, no I/O. Generated code (Plan 4) and the reasoner
(Plan 5) import these instead of re-deriving the algorithms.

CPM math runs in working-day offsets (integers). See the plan header for the
offset conventions.
"""

from typing import Dict, List, Tuple

from app.schemas.cpm import (
    Activity, CPMInput, CPMOutput, CPMResult, DependencyType,
)


class CircularDependencyError(ValueError):
    """Raised when the activity network contains a cycle."""


def topological_order(activities: List[Activity]) -> List[str]:
    """Activity ids in dependency order (Kahn's algorithm).

    Raises ValueError for an unknown predecessor, CircularDependencyError for
    a cycle. Ties broken by id, so the order is deterministic.
    """
    ids = {a.id for a in activities}
    indegree: Dict[str, int] = {a.id: 0 for a in activities}
    successors: Dict[str, List[str]] = {a.id: [] for a in activities}

    for a in activities:
        for dep in a.predecessors:
            if dep.predecessor_id not in ids:
                raise ValueError(
                    f"Activity '{a.id}' references unknown predecessor "
                    f"'{dep.predecessor_id}'"
                )
            indegree[a.id] += 1
            successors[dep.predecessor_id].append(a.id)

    queue = sorted(i for i, d in indegree.items() if d == 0)
    order: List[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for succ in successors[nid]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                queue.append(succ)
        queue.sort()

    if len(order) != len(activities):
        cycle = sorted(set(indegree) - set(order))
        raise CircularDependencyError(
            f"Circular dependency among: {', '.join(cycle)}"
        )
    return order
