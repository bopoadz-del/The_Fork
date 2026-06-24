"""Construction safety + QA/QC class registry. Source of truth: safety_classes.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

_REGISTRY_PATH = Path(__file__).parent / "safety_classes.json"


@dataclass(frozen=True)
class ClassEntry:
    id: int
    name: str
    category: Literal["safety", "qaqc"]
    definition: str
    active: bool
    weights_version: Optional[str]
    min_examples_required: int


def _parse_entries(raw: list) -> List[ClassEntry]:
    out = []
    for r in raw:
        out.append(ClassEntry(
            id=int(r["id"]),
            name=str(r["name"]),
            category=r["category"],
            definition=str(r.get("definition", "")),
            active=bool(r["active"]),
            weights_version=r.get("weights_version"),
            min_examples_required=int(r.get("min_examples_required", 30)),
        ))
    return out


def validate_registry(entries: List[ClassEntry]) -> None:
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for e in entries:
        if e.id in seen_ids:
            raise ValueError(f"duplicate id {e.id}")
        if e.name in seen_names:
            raise ValueError(f"duplicate name {e.name}")
        if e.category not in ("safety", "qaqc"):
            raise ValueError(f"invalid category {e.category} for {e.name}")
        if e.active and not e.weights_version:
            raise ValueError(f"class {e.name} is active but has no weights_version")
        seen_ids.add(e.id)
        seen_names.add(e.name)


def load_class_registry() -> List[ClassEntry]:
    raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = sorted(_parse_entries(raw), key=lambda e: e.id)
    validate_registry(entries)
    return entries


def get_active_classes() -> List[ClassEntry]:
    return [e for e in load_class_registry() if e.active]


def get_class_by_id(class_id: int) -> ClassEntry:
    for e in load_class_registry():
        if e.id == class_id:
            return e
    raise KeyError(f"class id {class_id} not in registry")


def get_class_by_name(name: str) -> ClassEntry:
    for e in load_class_registry():
        if e.name == name:
            return e
    raise KeyError(f"class name {name!r} not in registry")
