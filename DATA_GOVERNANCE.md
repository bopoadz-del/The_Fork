# Data Governance

How The Fork handles client documents — Roadmap V2 · Epic 6.

This document describes actual, implemented behaviour. It is kept honest: it
does not claim controls that are not in the code.

## What is stored, and where

| Data | Location | Notes |
|------|----------|-------|
| Uploaded documents | `DATA_DIR` (default `./data`) | Original files, UUID-prefixed |
| Project & document records | `DATA_DIR/projects.db` (SQLite) | Metadata, not file contents |
| Project memory (facts) | `DATA_DIR/projects.db` | Durable extracted facts |
| Custom document types | `DATA_DIR/custom_document_types.json` | |
| Saved workflows | `DATA_DIR/projects.db` | |
| Audit log | `DATA_DIR/audit.log` | Append-only JSONL |

There is **no external database and no third-party data processor.** Everything
stays on the host running the app.

## Retention

- Default: documents are kept **indefinitely**.
- Set `DATA_RETENTION_DAYS=<n>` to enable a retention window.
- `POST /v1/governance/purge` deletes documents (records **and** files) older
  than that window. Run it on a schedule (cron) to enforce retention.
- `GET /v1/governance` reports the current data directory and retention setting.

## Deletion on request

- `DELETE /v1/projects/{id}` removes the project, its document records, its
  project memory, **and the document files on disk** — it returns the count of
  files purged.
- `DELETE /v1/projects/{id}/documents/{doc_id}` removes one document (record +
  file).
- `DELETE /v1/projects/{id}/memory/{key}` forgets a single stored fact.

## Audit trail

Every create/upload/delete/purge is appended to `DATA_DIR/audit.log` with a UTC
timestamp. Read it per project via `GET /v1/projects/{id}/audit`. The log is
append-only — entries are never rewritten.

## Confidentiality

- All `/v1/*` endpoints require an API key (`Authorization: Bearer <key>`).
- The `cb_dev_key` development key works **only** in development/test
  environments — it is rejected when `ENV` is production.
- Project memory is scoped to a single project; facts never leak across
  projects.

## Known limitations (not yet implemented)

- **Encryption at rest** is not enabled. Files in `DATA_DIR` are stored in the
  clear. The `cryptography` dependency is installed; enabling at-rest
  encryption is tracked as follow-up work.
- No per-user access control beyond the API key (no row-level ownership).
- On the Render free tier there is no persistent disk — data placed in a temp
  directory does not survive a redeploy. Use a persistent volume for real data.
