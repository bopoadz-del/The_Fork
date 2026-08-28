"""Aconex / CDE connector — vendor-neutral interface, Aconex adapter, fake adapter.

Aconex (or another CDE) is the system of record. The Fork pulls, drafts, and
posts; it caches documents for RAG only. No Fork-owned RFI / submittal / VO /
punch / transmittal registers.
"""

from app.core.cde.config import (
    aconex_enabled,
    connector_mode,
    connector_note,
    default_cde_project_id,
    oauth_client_ready,
)
from app.core.cde.errors import CdeError, CdeNotConfiguredError
from app.core.cde.factory import get_cde_client, reset_cde_clients
from app.core.cde.fake import FakeCdeClient
from app.core.cde.ingest import ingest_cde_bytes, ingest_cde_document, post_rfi_draft, sync_cde_documents
from app.core.cde.protocol import CdeClient
from app.core.cde.types import CdeDocument, CdeMail, CdeMailDraft, CdePostResult

__all__ = [
    "CdeClient",
    "CdeDocument",
    "CdeError",
    "CdeMail",
    "CdeMailDraft",
    "CdeNotConfiguredError",
    "CdePostResult",
    "FakeCdeClient",
    "aconex_enabled",
    "connector_mode",
    "connector_note",
    "default_cde_project_id",
    "get_cde_client",
    "ingest_cde_bytes",
    "ingest_cde_document",
    "oauth_client_ready",
    "post_rfi_draft",
    "reset_cde_clients",
    "sync_cde_documents",
]
