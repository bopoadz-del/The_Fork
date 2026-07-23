"""Layered-RAG vocabulary + flag (docs/rag-deployment-plan.md).

Layers are the WHERE of knowledge; authority is the HOW-MUCH-IT-WINS.
Both are persisted per chunk (Alembic 0011) and default-inert until
RAG_LAYERED is set. See docs/superpowers/plans/2026-07-23-layered-rag.md.
"""
from __future__ import annotations

import os
import re

# L1 shared domain, L2A company/client rules, L2B live project record,
# L3 user/session. Names are the persisted ``chunks.layer`` values.
LAYERS = frozenset({"shared_domain", "company_rules", "project_record", "user_session"})
DEFAULT_LAYER = "shared_domain"

# Cross-cutting authority, highest precedence first. Contract beats meeting
# note; approved drawing beats draft; project BOQ beats generic benchmark.
AUTHORITIES = ("contractual", "design", "commercial", "operational",
               "policy", "historical", "personal")
DEFAULT_AUTHORITY = "operational"

# Legacy retrieval-time tags (STEP 0) map onto persisted layers so old
# rows and mixed corpora keep working during the migration.
LEGACY_LAYER_MAP = {
    "own": "project_record",
    "general_knowledge": "shared_domain",
    "master_corpus": "project_record",
    "user": "user_session",
}


def layered_enabled() -> bool:
    """Read RAG_LAYERED live so operators/tests can flip it without a
    re-import. Truthy: 1/true/yes/on (case-insensitive). Default: OFF."""
    return str(os.getenv("RAG_LAYERED", "")).strip().lower() in {"1", "true", "yes", "on"}


def authority_rank(name: str) -> int:
    """0 = strongest precedence; unknown authority sorts weakest."""
    try:
        return AUTHORITIES.index(name)
    except ValueError:
        return len(AUTHORITIES)


# ── ingest-time classification ─────────────────────────────────────────────

def _gk_project_ids() -> set:
    """Projects treated as the shared general-knowledge layer (curated_kb)."""
    raw = os.getenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    return {p.strip() for p in raw.split(",") if p.strip()}

# Doc-name -> authority, checked in this order (first hit wins). The label is
# what the doc IS, not where it lives: a priced BOQ is commercial whether it's a
# project doc or a reference. Patterns are deliberately conservative; anything
# unmatched falls back to a layer-appropriate default (see classify()).
_AUTHORITY_PATTERNS = (
    ("contractual", r"contract|agreement|conditions of contract|fidic|variation|"
                    r"\bvo\b|\bclaim\b|particular conditions|general conditions"),
    ("design", r"drawing|\bdwg\b|\bdxf\b|\bdgn\b|\bifc\b|\bga\b|layout|"
               r"\bplan\b|detail|\bsection\b|elevation|\bdesign\b"),
    ("commercial", r"\bboq\b|bill of quant|\brate\b|priced|tender|\bcost\b|"
                   r"estimat|valuation|\bipc\b|\bipa\b|payment|budget|cash[\s_-]?flow"),
    ("policy", r"procedure|\bprc[\s_-]|\btem[\s_-]|policy|method statement|"
               r"\bmethod\b|specification|\bspec\b|standard|\bcode\b|manual|"
               r"guideline|template|workflow|checklist"),
    ("operational", r"report|\blog\b|minutes|daily|weekly|\brfi\b|\bncr\b|"
                    r"inspection|submittal|schedule|programme|progress|transmittal"),
    ("historical", r"superseded|obsolete|archive|\bold\b|previous|deprecated"),
    ("personal", r"\bdraft\b|\bmemo\b|\bnote\b|working|scratch|personal"),
)

# When no doc-name keyword matches, the layer implies the authority: reference /
# rules content is policy-grade; project and user content is operational.
_LAYER_DEFAULT_AUTHORITY = {
    "shared_domain": "policy",
    "company_rules": "policy",
    "project_record": "operational",
    "user_session": "operational",
}

_COMPANY_RULE_NAME_RE = re.compile(
    r"procedure|\bprc[\s_-]|\btem[\s_-]|template|policy|workflow|guideline",
    re.IGNORECASE,
)


def _classify_authority(name: str) -> str:
    for authority, pattern in _AUTHORITY_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return authority
    return ""


def classify(project_id, doc_name, *, is_user_upload=False):
    """Return ``(knowledge_layer, authority)`` for a doc at ingest time.

    Layer: user uploads -> ``user_session``; docs in a general-knowledge project
    -> ``shared_domain`` (or ``company_rules`` when the name reads as a
    procedure/template/policy); everything else -> ``project_record``. Authority
    comes from doc-name keywords, else the layer's default. Pure and side-effect
    free so it is trivially testable and safe to call on every ingest."""
    name = doc_name or ""
    if is_user_upload:
        layer = "user_session"
    elif project_id in _gk_project_ids():
        layer = "company_rules" if _COMPANY_RULE_NAME_RE.search(name) else "shared_domain"
    else:
        layer = "project_record"
    authority = _classify_authority(name) or _LAYER_DEFAULT_AUTHORITY[layer]
    return layer, authority
