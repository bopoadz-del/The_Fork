"""Site-language routing that must not wait on embeddings.

Keyword routing after the 415/420 vocabulary pass still missed WIR, hold
point, IFC drawings (Issued For Construction — drawings, not a BIM file),
SI vs VO, nominated sub, and "delivery has slipped". These helpers are the
deterministic layer: classify the phrasing, then the orchestrator / reasoner
route or refuse to auto-dispatch.
"""
from __future__ import annotations

import re


# ── Formal instruments that must not collapse onto analysis templates ──

_ISSUE_VERB = r"(?:issue|raise|generate|prepare|draft|submit|file|open)"

_ISSUE_NCR = re.compile(
    rf"\b{_ISSUE_VERB}\s+(?:an?\s+)?ncr\b|\bncr\s+(?:for|against)\b",
    re.IGNORECASE,
)

_ISSUE_STOP_WORK = re.compile(
    rf"\b{_ISSUE_VERB}\s+(?:a\s+|an\s+)?stop[-\s]?work"
    r"|\bstop[-\s]?work\s+order\b",
    re.IGNORECASE,
)

_ISSUE_PC_CERT = re.compile(
    rf"\b{_ISSUE_VERB}\s+(?:a\s+|an\s+|the\s+)?"
    r"(?:pc|practical\s+completion)\s+cert"
    r"|\b(?:pc|practical\s+completion)\s+cert(?:ificate)?\b",
    re.IGNORECASE,
)

# ── Drawings vs BIM ──

_IFC_DRAWINGS = re.compile(
    r"\b(?:ifc\s+drawings?|issued\s+for\s+construction(?:\s+drawings?)?)\b",
    re.IGNORECASE,
)

_IFC_MODEL = re.compile(
    r"\b(?:ifc\s+model|\.ifc\b|bim\s+model|revit|navisworks)\b",
    re.IGNORECASE,
)

# ── SI is not a VO ──

_SITE_INSTRUCTION = re.compile(
    r"\b(?:site\s+instruction|\bsi[-/]\d|\bsi\s+\d)\b",
    re.IGNORECASE,
)

_VARIATION = re.compile(
    r"\b(?:variation(?:\s+order)?|change\s+order|\bvo[-/]?\d|\bvo\b)\b",
    re.IGNORECASE,
)

# ── Hold point / WIR / nominated sub / slipped delivery (presence checks) ──

_HOLD_POINT = re.compile(r"\bhold[- ]points?\b", re.IGNORECASE)
_WIR = re.compile(
    r"\b(?:wir|work\s+inspection\s+request)\b",
    re.IGNORECASE,
)
_NOMINATED_SUB = re.compile(
    r"\bnominated\s+sub(?:contractor|contract)?\b",
    re.IGNORECASE,
)
_DELIVERY_SLIPPED = re.compile(
    r"\bdelivery\s+(?:has\s+)?slipped\b|\bhas\s+slipped\b",
    re.IGNORECASE,
)


def message_issues_ncr(text: str) -> bool:
    return bool(_ISSUE_NCR.search(text or ""))


def message_issues_stop_work(text: str) -> bool:
    return bool(_ISSUE_STOP_WORK.search(text or ""))


def message_issues_pc_cert(text: str) -> bool:
    return bool(_ISSUE_PC_CERT.search(text or ""))


def message_wants_ifc_drawings(text: str) -> bool:
    """Issued-For-Construction drawings — not an IFC/BIM model file."""
    raw = text or ""
    if not _IFC_DRAWINGS.search(raw):
        return False
    if _IFC_MODEL.search(raw):
        return False
    return True


def message_is_site_instruction_not_vo(text: str) -> bool:
    """A site instruction is not a variation order unless both are named."""
    raw = text or ""
    if not _SITE_INSTRUCTION.search(raw):
        return False
    return not _VARIATION.search(raw)


def message_wants_hold_point(text: str) -> bool:
    return bool(_HOLD_POINT.search(text or ""))


def message_wants_wir(text: str) -> bool:
    return bool(_WIR.search(text or ""))


def message_wants_nominated_sub(text: str) -> bool:
    return bool(_NOMINATED_SUB.search(text or ""))


def message_reports_slipped_delivery(text: str) -> bool:
    return bool(_DELIVERY_SLIPPED.search(text or ""))


_CLASH_CDE_RFI = re.compile(
    r"\b(?:rfi|request\s+for\s+information|post\s+(?:an?\s+)?rfi|raise\s+(?:an?\s+)?rfi)\b",
    re.IGNORECASE,
)


def message_wants_clash_cde_rfi(text: str) -> bool:
    """Clash follow-up posts to the CDE — not a local Fork RFI number."""
    return bool(_CLASH_CDE_RFI.search(text or ""))
