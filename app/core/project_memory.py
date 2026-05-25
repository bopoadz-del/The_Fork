"""Project memory — durable facts accumulated across a project's documents.

Roadmap V2 · Epic 3 (Project memory).

Documents are no longer processed in isolation: when one is analysed, the
durable facts it yields (contract value, key dates, parties, BOQ totals…) are
written to the project, and later questions can be answered from that
accumulated knowledge without re-attaching the source document.
"""

from typing import Any, Dict, List, Optional

from app.core import projects as store

# Durable fields worth remembering, mapped to a human label.
DURABLE_FIELDS: Dict[str, str] = {
    "contract_value": "Contract value",
    "contract_type": "Contract type",
    "commencement_date": "Commencement date",
    "completion_date": "Completion date",
    "ld_rate": "Liquidated damages rate",
    "liquidated_damages": "Liquidated damages",
    "defects_liability_period": "Defects liability period",
    "retention_percent": "Retention percentage",
    "employer": "Employer",
    "contractor": "Contractor",
    "drawing_number": "Drawing number",
    "revision": "Revision",
    "scale": "Drawing scale",
    "boq_total": "BOQ total",
    "project_name": "Project name",
}

_JUNK = {"", "none", "null", "n/a", "unknown", "0", "0.0"}


def extract_facts(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """Pull durable facts from an analysis result (depth-limited dict scan)."""
    found: Dict[str, str] = {}

    def scan(node: Any, depth: int = 0) -> None:
        if depth > 2 or not isinstance(node, dict):
            return
        for k, v in node.items():
            if k in DURABLE_FIELDS and isinstance(v, (str, int, float, bool)):
                sv = str(v).strip()
                if sv.lower() not in _JUNK:
                    found.setdefault(k, sv)
            elif isinstance(v, dict):
                scan(v, depth + 1)

    scan(result)
    return [
        {"key": k, "value": v, "label": DURABLE_FIELDS[k]}
        for k, v in found.items()
    ]


def remember_from_result(
    project_id: str,
    result: Dict[str, Any],
    source_document: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract durable facts from a result and persist them to project memory."""
    saved = []
    for fact in extract_facts(result):
        saved.append(
            store.set_fact(
                project_id, fact["key"], fact["value"],
                source_document=source_document,
            )
        )
    return saved


def build_project_context(project_id: str, query: str = "", limit: int = 8) -> str:
    """A combined context block with project facts + document listing.

    Returns a non-empty string when the project has facts or documents.
    Returns "" when neither exists.
    """
    facts_block = build_memory_context(project_id, query, limit)
    docs = store.list_documents(project_id)

    if not facts_block and not docs:
        return ""

    parts: List[str] = []
    if facts_block:
        parts.append(facts_block)
    if docs:
        lines = ["Project documents:"]
        for doc in docs:
            lines.append(
                f"- {doc['original_name']} (type: {doc['doc_type']}, role: {doc['doc_role']})"
            )
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def build_memory_context(project_id: str, query: str = "", limit: int = 8) -> str:
    """A compact text block of relevant project facts, for injection into chat.

    With a query, returns the matching facts; without one, the whole memory.
    Empty string when the project has no facts yet.
    """
    facts = (
        store.search_facts(project_id, query) if query
        else store.list_facts(project_id)
    )
    if not facts:
        return ""
    lines = ["Known facts about this project:"]
    for f in facts[:limit]:
        label = DURABLE_FIELDS.get(
            f["key"], f["key"].replace("_", " ").capitalize()
        )
        lines.append(f"- {label}: {f['value']}")
    return "\n".join(lines)
