"""Layered-RAG vocabulary + flag (docs/rag-deployment-plan.md).

Layers are the WHERE of knowledge; authority is the HOW-MUCH-IT-WINS.
Both are persisted per chunk (Alembic 0011) and default-inert until
RAG_LAYERED is set. See docs/superpowers/plans/2026-07-23-layered-rag.md.
"""
from __future__ import annotations

import os

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
