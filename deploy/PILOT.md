# Pilot deployment — Diriyah BOQ (post-migration)

Runbook for standing up The Fork on managed Postgres after merging the
Phases 0–4 migration branch. Assumes construction kit enabled and SQLite
data migrated from an existing `./data` tree.

## Prerequisites

- Postgres 16 with **pgvector** (Render Postgres, Supabase, or `docker compose up`)
- `REDIS_URL` for multi-worker (`start.sh` uses 2 workers when set)
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
| `REDIS_URL` | Shared Redis for 2 workers |
| `SENTRY_DSN` | Project DSN |

## 4. Boot and smoke

```bash
./start.sh
# or: docker compose up -d cerebrum-blocks

./scripts/pilot_smoke.sh
```

## 5. Pilot exit criteria (brief)

- 2 weeks uptime with zero data-loss incidents
- Forced exception visible in Sentry
- Restore-from-backup drill performed once (document timestamp + row counts)
- Diriyah BOQ dataset exercised end-to-end (upload → index → chat/RAG)

## Rollback

- Keep legacy `DATA_DIR/*.db` until pilot sign-off
- To roll back app only: unset `DATABASE_URL` and restart (SQLite fallback at
  `DATA_DIR/the_fork.db` — empty until re-migrated)
