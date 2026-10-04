# Ingest exclusion rule — the RAG takes text formats only

**Read this before touching any ingest path.**

The knowledge base is text-only by design. From
[`RAG_GAPS_REVIEW_2026-09-12.md` §E](RAG_GAPS_REVIEW_2026-09-12.md):

> **Excluded BY DESIGN — never ingested, not gaps:** CAD (`.dwg/.dxf`),
> images (`.jpg/.png`), Google Earth (`.kmz/.kml`), video, GIS internals,
> fonts. Only text formats produce retrievable chunks.

Photos are not corpus material either
([`PHOTO_RAG_STATUS.md`](PHOTO_RAG_STATUS.md)): a photo attached in chat is
question context (`POST /v1/chat/analyze-photo`), never a document.

## The single declaration

`app/core/ingest_status.py` → **`TEXT_BEARING_EXTS`** (with
`is_ingestible()`). It is the only list that decides whether a file may be
ingested. Everything else derives from it:

| entry point | how it applies the rule |
|---|---|
| Drive ingest (`scripts/p1b_ingest_drive_server.py`) | non-text files are filtered at discovery: never assigned, downloaded, stored or archived; counted in `accounting.unsupported_by_format` |
| Indexer (`doc_index._SUPPORTED_EXTS`) | *is* `TEXT_BEARING_EXTS` |
| Archive members (`doc_index._extract_archive`) | a non-text member is skipped before it is read |
| Project document upload (`POST /v1/projects/{id}/documents`) | 415 with the rule's message |
| `/upload` with a `project_id` | 415 before anything is written |
| Drive walker + Drive import (`app/routers/drive.py`) | allow-list is `TEXT_BEARING_EXTS`; import returns 415 |
| CDE ingest (`app/core/cde/ingest.py`) | refused with the rule's message |

Session/sandbox uploads (no project) and chat photos keep their own
acceptance: they are context for one conversation, never persisted into the
knowledge base.

## Why this page exists

2026-10-04: a Drive ingest run downloaded, encrypted and registered 8 drone
videos (2 GB), hundreds of DWG drawings and photos into a client project.
None of it could ever be retrieved, and the multi-hundred-MB files
OOM-killed the ingest repeatedly while effort went into engineering around
them. The first question about any file that breaks an ingest is
**"should this file be here at all?"**
