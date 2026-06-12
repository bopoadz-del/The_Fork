# Pilot deployment — Diriyah BOQ (post-migration)

Runbook for standing up The Fork on managed Postgres after merging the
Phases 0–4 migration branch. Assumes construction kit enabled and SQLite
data migrated from an existing `./data` tree.

## Prerequisites

- Postgres 16 with **pgvector** (Render Postgres, Supabase, or `docker compose up`)
- `UVICORN_WORKERS` for uvicorn worker count (default `1`; sole worker knob)
- `REDIS_URL` optional — shared sessions/rate limits when using multiple workers
- `SENTRY_DSN` for error tracking (optional but recommended for pilot)
- Construction kit: `CEREBRUM_VIRGIN=false` and `CEREBRUM_DOMAIN_KITS=construction`

## 1. Provision database

```bash
# Local smoke (from repo root)
docker compose up -d postgres
export DATABASE_URL=postgresql+psycopg://thefork:thefork@localhost:5432/thefork
alembic upgrade head
```

On Render/Supabase: create the instance, enable automated backups, copy the
connection string into `DATABASE_URL` (use `postgresql+psycopg://` for SQLAlchemy).

## 2. Migrate legacy SQLite (if upgrading existing host)

```bash
export DATA_DIR=./data   # directory with legacy *.db files
export DATABASE_URL=postgresql+psycopg://...

python scripts/migrate_sqlite_to_pg.py --dry-run   # row counts only
python scripts/migrate_sqlite_to_pg.py --execute   # idempotent insert
```

Compare printed row counts with legacy `sqlite3` counts before cutover.

## 3. Configure environment

Copy `.env.example` → `.env` and set at minimum:

| Variable | Pilot value |
|----------|-------------|
| `DATABASE_URL` | Postgres connection string |
| `CEREBRUM_VIRGIN` | `false` |
| `CEREBRUM_DOMAIN_KITS` | `construction` |
| `DATA_DIR` | Persistent volume for uploads |
| `UVICORN_WORKERS` | `1` on Render starter; `2` only after RAM upgrade |
| `REDIS_URL` | Shared Redis for sessions/rate limits (optional) |
| `SENTRY_DSN` | Project DSN |
| `RAG_EMBEDDING_MODEL` | **Unset** on Render (model2vec 256-dim default) or `minishlab/potion-base-8M` |

## 4. Boot and smoke

```bash
./start.sh
# or: docker compose up -d cerebrum-blocks

./scripts/pilot_smoke.sh
```

## Current status (2026-06-12)

| Item | Status |
|------|--------|
| Render web (`the-fork`) | Live — 45 blocks, construction kit on |
| Postgres `the-fork-db` (PG 16) | Provisioned; `DATABASE_URL` set (internal) |
| Alembic on boot | `entrypoint.sh` runs `python -m alembic upgrade head` |
| `UVICORN_WORKERS` | `1` on Render starter (sole worker knob; `2` OOMs at 512Mi) |
| `REDIS_URL` | `cerebrum-redis` resumed (shared rate limits when workers > 1) |
| `SENTRY_DSN` | **Not set** — blocker for pilot clock; see Sentry gate below |
| SQLite → Postgres cutover | **Done** 2026-06-12 — 56 documents, 141 chunks in Postgres |
| `chunks.embedding` | **Confirmed** `vector(256)` via pilot-preflight |
| Doc re-index / Diriyah E2E | **RAG gate passed** — D999.14 @ 1,060, D999.15 @ 1,288, Part 3 summary 1,852,848 |
| Backup drill | **Done** — PITR restore `the-fork-db-drill-20260612` (snapshot @ 2026-06-12T15:22:13Z) |
| 2-week pilot clock | **Pending Sentry smoke** |

### Production admin ops (disk-backed)

One-off Render jobs do not mount `/app/data`. Run cutover from the web process:

```bash
# After deploy with admin endpoints + CEREBRUM_MASTER_KEY set:
curl -sS -X POST -H "Authorization: Bearer $CEREBRUM_MASTER_KEY" \
  "https://the-fork.onrender.com/v1/admin/debug/migrate-sqlite?dry_run=true"

curl -sS -H "Authorization: Bearer $CEREBRUM_MASTER_KEY" \
  "https://the-fork.onrender.com/v1/admin/debug/pilot-preflight"

curl -sS -X POST -H "Authorization: Bearer $CEREBRUM_MASTER_KEY" \
  "https://the-fork.onrender.com/v1/admin/debug/sentry-smoke"
```

**81 users** in SQLite dry-run likely includes test-account accumulation — prune or document before `execute=true`.

### Pilot ops log

#### 2026-06-12T16:16:00Z — Cutover, schema, RAG, backup drill

| Gate | Result |
|------|--------|
| Schema | `chunks.embedding` = `vector(256)` |
| Cutover dry-run (prod volume) | 3 users, 6 projects, 56 documents, 165 chunks would migrate (not 81 users — that was local `./data`) |
| Cutover execute | Inserted 56 documents, 141 chunks (24 orphan chunks skipped — missing `documents` rows) |
| Re-index | `project-reindex` on `3f6f28b2` → 8 chunks indexed |
| Diriyah BOQ RAG | `POST /v1/rag/search` retrieves chunk `3f6f28b2:76e63ed2:23` with D999.14 @ 1,060.00 and D999.15 @ 1,288.00; chunk `:50` has Part 3 total 1,852,848.00 |
| Backup drill | PITR restore to `the-fork-db-drill-20260612` succeeded; scratch DB `available` (pre-cutover snapshot — expect documents/chunks = 0 vs live 56/141) |

**Sentry (open):** No `SENTRY_AUTH_TOKEN` in agent environment — create project in Sentry UI, set `SENTRY_DSN` on Render, redeploy, then `POST /v1/admin/debug/sentry-smoke`.

**Ops auth:** `CEREBRUM_MASTER_KEY` set on Render for admin endpoints (rotate after pilot setup).

#### Pilot clock

Starts when Sentry smoke passes. Target end: **2026-06-26** (2 weeks after clock start).

## 5. Pilot exit criteria (brief)

- 2 weeks uptime with zero data-loss incidents
- Forced exception visible in Sentry
- Restore-from-backup drill performed once (document timestamp + row counts)
- Diriyah BOQ dataset exercised end-to-end (upload → index → chat/RAG)

## Rollback

- Keep legacy `DATA_DIR/*.db` until pilot sign-off
- To roll back app only: unset `DATABASE_URL` and restart (SQLite fallback at
  `DATA_DIR/the_fork.db` — empty until re-migrated)
