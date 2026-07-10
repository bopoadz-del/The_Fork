"""Procedure orchestrator actions → PRC metadata and optional delegate handlers.

SmartOrchestratorBlock routes user language to procedure-specific action names
(design_review_workflow, rfi_management, …). ConstructionContainer.route must
never return Unknown action for these — either delegate to a real handler or
return honest metadata-only guidance from the procedures knowledge base.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# orchestrator action → (PRC id, optional ConstructionContainer delegate action)
PROCEDURE_ACTION_MAP: Dict[str, Tuple[str, Optional[str]]] = {
    "design_review_workflow": ("PRC-501", None),
    "design_directive": ("PRC-502", None),
    "rfi_management": ("PRC-301", "rfi_generator"),
    "work_package_control": ("PRC-303", None),
    "qa_audit": ("PRC-401", "qa_qc_inspection"),
    "ncr_management": ("PRC-402", "qa_qc_inspection"),
    "handover_management": ("PRC-404", "commissioning_checklist"),
    "inspection_request": ("PRC-405", "qa_qc_inspection"),
    "job_requisition": ("PRC-601", None),
    "rfp_management": ("PRC-602", None),
    "contract_award": ("PRC-604", None),
}


def is_procedure_action(action: str) -> bool:
    return action in PROCEDURE_ACTION_MAP


def procedure_metadata(action: str) -> Dict[str, Any]:
    """Return honest metadata-only payload for a procedure action."""
    from app.core.construction_knowledge import ConstructionKnowledge

    prc_id, delegate = PROCEDURE_ACTION_MAP.get(action, (None, None))
    if not prc_id:
        return {
            "status": "error",
            "error": f"Unknown procedure action: {action}",
        }
    ck = ConstructionKnowledge()
    proc = ck.get_procedure(prc_id) or {}
    return {
        "status": "success",
        "action": action,
        "execution_mode": "metadata_only",
        "procedure_id": prc_id,
        "procedure_title": proc.get("title", ""),
        "purpose": proc.get("purpose", ""),
        "roles": proc.get("roles") or {},
        "statuses": proc.get("statuses") or [],
        "rules": proc.get("rules") or [],
        "required_fields": proc.get("required_fields") or [],
        "delegate_action": delegate,
        "note": (
            "Procedure guidance from the knowledge base — not a fabricated "
            "execution result. Use delegate_action when a runnable handler exists."
        ),
    }


def resolve_procedure_route(action: str) -> Tuple[str, Optional[str]]:
    """Return (prc_id, delegate_action) for an orchestrator procedure action."""
    return PROCEDURE_ACTION_MAP.get(action, (None, None))  # type: ignore[return-value]
