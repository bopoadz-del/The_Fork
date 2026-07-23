"""
construction_knowledge.py
==========================
Live knowledge module for the construction platform.
Any block imports this to get procedure rules, validate inputs,
enforce critical business rules, and generate correct document numbers.

Place at: app/core/construction_knowledge.py

Usage:
    from app.core.construction_knowledge import (
        ConstructionKnowledge,
        validate_design_status,
        generate_doc_number,
        get_procedure,
        enforce_critical_rules,
    )
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# LOAD PROCEDURES DATABASE
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).parent.parent / "data" / "procedures" / "procedures_db.json"
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "construction_expert.txt"

_procedures_db: Optional[Dict] = None


def _load_db() -> Dict:
    global _procedures_db
    if _procedures_db is None:
        if _DB_PATH.exists():
            _procedures_db = json.loads(_DB_PATH.read_text(encoding="utf-8"))
        else:
            _procedures_db = {}
    return _procedures_db


def get_procedure(procedure_id: str) -> Optional[Dict]:
    """Return the full procedure dict for a given PRC number e.g. 'PRC-402'."""
    db = _load_db()
    return db.get("procedures", {}).get(procedure_id)


def get_system_prompt() -> str:
    """Return the construction expert system prompt text for injection into chat."""
    if _SYSTEM_PROMPT_PATH.exists():
        return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# CRITICAL BUSINESS RULES
# ---------------------------------------------------------------------------

CRITICAL_RULES = {

    "no_approved_on_design": {
        "rule": "Never use 'APPROVED' on design documents.",
        "correct": ["accepted", "for comment", "buy-off"],
        "procedure": "PRC-501",
        "violation_message": (
            "The word 'APPROVED' is contractually prohibited on design documents. "
            "Use 'accepted', 'for comment', or 'buy-off' instead."
        ),
    },

    "no_work_before_dd_approval": {
        "rule": "No work may proceed on any instruction until the Design Directive has Employer approval.",
        "procedure": "PRC-502",
        "violation_message": (
            "Work cannot proceed on this instruction. "
            "A Design Directive (DD) must be approved by the Employer first (PRC-502)."
        ),
    },

    "rfm_not_vo": {
        "rule": "An RFM is an instruction only - NOT a Variation Order. Only a signed VO changes the contract.",
        "procedure": "PRC-606",
        "violation_message": (
            "An RFM (Request for Modification) is an instruction, not a contract amendment. "
            "A signed Variation Order (VO) is required to change the contract (PRC-606)."
        ),
    },

    "stop_work_resumption": {
        "rule": "No work resumption after STOP WORK without PMC Project Director written sign-off.",
        "procedure": "PRC-406",
        "violation_message": (
            "Work cannot resume after a STOP WORK order without the PMC Project Director's "
            "written sign-off (PRC-406)."
        ),
    },

    "payment_form_controlled": {
        "rule": "Payment Request Form is a controlled document. Cannot be modified without VP Programme Management approval.",
        "procedure": "PRC-605",
        "violation_message": (
            "The Payment Request Form is a controlled document and cannot be modified "
            "without VP Programme Management approval (PRC-605)."
        ),
    },

    "ncr_not_ir": {
        "rule": "An NCR (non-conformance) and an Inspection Rejection (IR) are different.",
        "procedure": "PRC-402/PRC-405",
        "violation_message": (
            "An Inspection Rejection (PRC-405) is a routine hold that may or may not "
            "escalate to an NCR. An NCR (PRC-402) is a formal non-conformance record."
        ),
    },

    "design_review_min_distribution": {
        "rule": "Design review package must be distributed at least 7 calendar days before the workshop.",
        "procedure": "PRC-501",
        "violation_message": (
            "The design review package must be distributed a minimum of 7 calendar days "
            "before the workshop date (PRC-501)."
        ),
    },
}


def enforce_critical_rules(text: str) -> List[Dict]:
    """
    Scan text for critical rule violations.
    Returns a list of violation dicts: {rule_id, message, procedure}.
    """
    violations = []
    text_lower = text.lower()

    # Check for forbidden "APPROVED" on design docs
    if re.search(r"\bapprove[ds]?\b|\bapproval\b", text_lower):
        design_context_keywords = [
            "design", "drawing", "package", "review", "consultant",
            "architect", "engineer", "document"
        ]
        if any(kw in text_lower for kw in design_context_keywords):
            violations.append({
                "rule_id": "no_approved_on_design",
                **CRITICAL_RULES["no_approved_on_design"]
            })

    return violations


# ---------------------------------------------------------------------------
# DOCUMENT NUMBER GENERATION
# ---------------------------------------------------------------------------

def generate_doc_number(doc_type: str, sequence: int, year: Optional[int] = None) -> str:
    """
    Generate a correctly formatted document number.

    Examples:
        generate_doc_number("RFI", 42)       -> "RFI-0042"
        generate_doc_number("NCR", 1, 2024)  -> "NCR-2024-001"
        generate_doc_number("VO", 15)        -> "VO-015"
        generate_doc_number("DD", 23)        -> "DD-023"
    """
    templates = {
        "RFI": f"RFI-{sequence:04d}",
        "NCR": f"NCR-{year}-{sequence:03d}" if year else f"NCR-{sequence:03d}",
        "IR":  f"IR-{sequence:04d}",
        "VO":  f"VO-{sequence:03d}",
        "RFM": f"RFM-{sequence:03d}",
        "JR":  f"JR-{sequence:04d}",
        "PDN": f"PDN-{year}-{sequence:03d}" if year else f"PDN-{sequence:03d}",
        "DD":  f"DD-{sequence:03d}",
        "PR":  f"PR-{sequence:04d}",
        "WP":  f"WP-{sequence:04d}",
    }
    return templates.get(doc_type.upper(), f"{doc_type.upper()}-{sequence:04d}")


# ---------------------------------------------------------------------------
# DESIGN REVIEW VALIDATION (PRC-501)
# ---------------------------------------------------------------------------

VALID_DESIGN_STATUSES = {"FOR_COMMENT", "ACCEPTANCE", "BUY_OFF", "PENDING_DEBRIEF", "SUPERSEDED"}
FORBIDDEN_DESIGN_STATUSES = {"APPROVED", "APPROVAL", "SIGN_OFF"}


def validate_design_status(status: str) -> Tuple[bool, str]:
    """
    Validate a design review status.
    Returns (is_valid, message).
    """
    s = status.upper().replace(" ", "_").replace("-", "_")
    if s in FORBIDDEN_DESIGN_STATUSES:
        return (
            False,
            f"'{status}' is forbidden on design documents (PRC-501). "
            f"Use one of: {', '.join(VALID_DESIGN_STATUSES)}"
        )
    if s not in VALID_DESIGN_STATUSES:
        return (
            False,
            f"'{status}' is not a valid design review status. "
            f"Valid statuses: {', '.join(VALID_DESIGN_STATUSES)}"
        )
    return True, f"Status '{status}' is valid."


def check_review_timeline(distribution_date: str, workshop_date: str) -> Tuple[bool, str]:
    """
    Check that the review distribution period meets PRC-501 requirements.
    distribution_date and workshop_date: 'YYYY-MM-DD' strings.
    Returns (compliant, message).
    """
    from datetime import date, timedelta
    try:
        dist = date.fromisoformat(distribution_date)
        workshop = date.fromisoformat(workshop_date)
        delta = (workshop - dist).days
        if delta < 7:
            return (
                False,
                f"Only {delta} calendar days between distribution and workshop. "
                f"PRC-501 requires minimum 7 days. Workshop must be no earlier than "
                f"{(dist + timedelta(days=7)).isoformat()}."
            )
        if delta > 14:
            return (
                True,
                f"Distribution period is {delta} days. PRC-501 maximum is 14 days - "
                f"consider whether the package can be issued closer to the workshop."
            )
        return True, f"Timeline compliant: {delta} calendar days distribution period."
    except Exception as e:
        return False, f"Could not parse dates: {e}"


# ---------------------------------------------------------------------------
# NCR VALIDATION (PRC-402)
# ---------------------------------------------------------------------------

VALID_NCR_DISPOSITIONS = {"USE_AS_IS", "REPAIR", "REJECT", "CONCESSION"}
NCR_WORKFLOW_SEQUENCE = [
    "RAISED", "ACKNOWLEDGED", "DISPOSITION_PROPOSED",
    "DISPOSITION_REVIEWED", "APPROVED", "IMPLEMENTING", "VERIFYING", "CLOSED"
]


def validate_ncr_disposition(disposition: str) -> Tuple[bool, str]:
    d = disposition.upper().replace(" ", "_").replace("-", "_")
    if d not in VALID_NCR_DISPOSITIONS:
        return (
            False,
            f"'{disposition}' is not a valid NCR disposition (PRC-402). "
            f"Valid options: {', '.join(VALID_NCR_DISPOSITIONS)}"
        )
    descriptions = {
        "USE_AS_IS": "Non-conformance acceptable without repair",
        "REPAIR": "Bring into conformance by rework",
        "REJECT": "Remove and replace",
        "CONCESSION": "Waiver from Employer required",
    }
    return True, f"Valid disposition: {d} - {descriptions[d]}"


def next_ncr_status(current_status: str) -> Optional[str]:
    """Return the next status in the NCR workflow sequence."""
    current = current_status.upper()
    try:
        idx = NCR_WORKFLOW_SEQUENCE.index(current)
        return NCR_WORKFLOW_SEQUENCE[idx + 1] if idx + 1 < len(NCR_WORKFLOW_SEQUENCE) else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# RISK SCORING (PRC-302)
# ---------------------------------------------------------------------------

def score_risk(probability: int, impact: int) -> Dict:
    """
    Score a risk per PRC-302 methodology.
    probability: 1-5, impact: 1-5
    Returns dict with score, band, and formatted statement.
    """
    if not (1 <= probability <= 5 and 1 <= impact <= 5):
        return {"error": "Probability and impact must each be 1-5"}
    score = probability * impact
    if score <= 4:
        band = "GREEN"
    elif score <= 9:
        band = "AMBER"
    else:
        band = "RED"
    return {
        "probability": probability,
        "impact": impact,
        "score": score,
        "band": band,
        "requires_action": band in ("AMBER", "RED"),
        "description": f"Risk Score {score}/25 - {band}",
    }


# ---------------------------------------------------------------------------
# PAYMENT CALCULATIONS (PRC-605)
# ---------------------------------------------------------------------------

def calculate_payment(
    claimed_amount: float,
    certified_amount: float,
    retention_rate: float = 0.05,
    cumulative_previous_certified: float = 0.0,
    contract_value: float = 0.0,
) -> Dict:
    """
    Calculate net payment due per PRC-605.
    """
    retention_held = certified_amount * retention_rate
    net_due = certified_amount - retention_held
    cumulative_now = cumulative_previous_certified + certified_amount
    pct_complete = (cumulative_now / contract_value * 100) if contract_value else None

    return {
        "claimed_amount": claimed_amount,
        "certified_amount": certified_amount,
        "retention_held": round(retention_held, 2),
        "net_payment_due": round(net_due, 2),
        "cumulative_certified": round(cumulative_now, 2),
        "percent_complete": round(pct_complete, 1) if pct_complete is not None else None,
        "disputed_amount": round(claimed_amount - certified_amount, 2),
        "retention_rate_pct": retention_rate * 100,
    }


# ---------------------------------------------------------------------------
# EVM CALCULATIONS (construction standard)
# ---------------------------------------------------------------------------

def calculate_evm(
    bac: float,       # Budget at Completion
    bcwp: float,      # Budgeted Cost of Work Performed (Earned Value)
    bcws: float,      # Budgeted Cost of Work Scheduled (Planned Value)
    acwp: float,      # Actual Cost of Work Performed
) -> Dict:
    """
    Earned Value Management calculations - standard construction formula set.
    """
    cpi = round(bcwp / acwp, 3) if acwp else None
    spi = round(bcwp / bcws, 3) if bcws else None
    eac = round(bac / cpi, 2) if cpi else None
    etc = round(eac - acwp, 2) if eac is not None else None
    vac = round(bac - eac, 2) if eac is not None else None
    cv = round(bcwp - acwp, 2)
    sv = round(bcwp - bcws, 2)

    return {
        "BAC": bac, "BCWP": bcwp, "BCWS": bcws, "ACWP": acwp,
        "CPI": cpi,
        "SPI": spi,
        "EAC": eac,
        "ETC": etc,
        "VAC": vac,
        "CV": cv,
        "SV": sv,
        "status": {
            "cost": "UNDER BUDGET" if cv >= 0 else "OVER BUDGET",
            "schedule": "AHEAD" if sv >= 0 else "BEHIND",
            "cpi_health": "GOOD" if cpi and cpi >= 1 else ("WARNING" if cpi and cpi >= 0.9 else "CRITICAL"),
        }
    }


# ---------------------------------------------------------------------------
# TENDER EVALUATION (PRC-603)
# ---------------------------------------------------------------------------

def evaluate_tender(
    tenderers: List[Dict],
    weights: Optional[Dict] = None,
) -> Dict:
    """
    Score and rank tender submissions per PRC-603.

    tenderers: list of dicts, each with:
        {
            "name": str,
            "technical_score": float (0-100),
            "commercial_score": float (0-100),
            "hse_score": float (0-100),
            "local_content_score": float (0-100),  # optional
        }

    weights: optional dict overriding defaults:
        {"technical": 0.45, "commercial": 0.45, "hse": 0.07, "local_content": 0.03}
    """
    if weights is None:
        weights = {"technical": 0.45, "commercial": 0.45, "hse": 0.07, "local_content": 0.03}

    scored = []
    for t in tenderers:
        total = (
            t.get("technical_score", 0) * weights["technical"]
            + t.get("commercial_score", 0) * weights["commercial"]
            + t.get("hse_score", 0) * weights["hse"]
            + t.get("local_content_score", 0) * weights.get("local_content", 0)
        )
        scored.append({**t, "weighted_total": round(total, 2)})

    ranked = sorted(scored, key=lambda x: x["weighted_total"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1

    return {
        "ranked_tenderers": ranked,
        "recommended": ranked[0] if ranked else None,
        "weights_applied": weights,
        "procedure": "PRC-603",
    }


# ---------------------------------------------------------------------------
# MAIN KNOWLEDGE CLASS (convenience wrapper)
# ---------------------------------------------------------------------------

class ConstructionKnowledge:
    """
    Single import point for all construction domain knowledge.
    Blocks instantiate this once and call methods as needed.

    Example:
        from app.core.construction_knowledge import ConstructionKnowledge
        ck = ConstructionKnowledge()

        # Validate design status
        ok, msg = ck.validate_design_status("APPROVED")  # -> False, violation message

        # Generate a doc number
        num = ck.generate_doc_number("NCR", 1, year=2024)  # -> "NCR-2024-001"

        # Get full procedure rules
        proc = ck.get_procedure("PRC-402")  # -> dict with all NCR rules

        # Score a risk
        risk = ck.score_risk(4, 3)  # -> {score: 12, band: "RED", ...}

        # Get system prompt for chat injection
        prompt = ck.get_system_prompt()
    """

    def validate_design_status(self, status: str) -> Tuple[bool, str]:
        return validate_design_status(status)

    def check_review_timeline(self, distribution_date: str, workshop_date: str) -> Tuple[bool, str]:
        return check_review_timeline(distribution_date, workshop_date)

    def validate_ncr_disposition(self, disposition: str) -> Tuple[bool, str]:
        return validate_ncr_disposition(disposition)

    def next_ncr_status(self, current_status: str) -> Optional[str]:
        return next_ncr_status(current_status)

    def score_risk(self, probability: int, impact: int) -> Dict:
        return score_risk(probability, impact)

    def calculate_payment(self, claimed: float, certified: float, **kwargs) -> Dict:
        return calculate_payment(claimed, certified, **kwargs)

    def calculate_evm(self, bac, bcwp, bcws, acwp) -> Dict:
        return calculate_evm(bac, bcwp, bcws, acwp)

    def evaluate_tender(self, tenderers: List[Dict], weights: Optional[Dict] = None) -> Dict:
        return evaluate_tender(tenderers, weights)

    def generate_doc_number(self, doc_type: str, sequence: int, year: Optional[int] = None) -> str:
        return generate_doc_number(doc_type, sequence, year)

    def enforce_critical_rules(self, text: str) -> List[Dict]:
        return enforce_critical_rules(text)

    def get_procedure(self, procedure_id: str) -> Optional[Dict]:
        return get_procedure(procedure_id)

    def get_system_prompt(self) -> str:
        return get_system_prompt()

    def get_workflow(self, procedure_id: str) -> Optional[List[str]]:
        proc = get_procedure(procedure_id)
        if not proc:
            return None
        return proc.get("workflow") or proc.get("workflow_type_a")

    def get_roles(self, procedure_id: str) -> Optional[Dict]:
        proc = get_procedure(procedure_id)
        return proc.get("roles") if proc else None

    def get_critical_rule(self, rule_id: str) -> Optional[Dict]:
        return CRITICAL_RULES.get(rule_id)

    def list_procedures(self) -> List[str]:
        db = _load_db()
        return list(db.get("procedures", {}).keys())
