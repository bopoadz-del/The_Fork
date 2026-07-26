# Redis Program — Fork-Tailored Audit (plan only, not built)

2026-07-26. Audits the generic 5-phase Redis program against **what The_Fork
already has**, so the eventual build is a small delta, not greenfield. No code
was changed by this audit.

## Boundary law (unchanged, already how the repo is wired)

**Postgres is truth. Redis is ephemeral. Nothing in Redis is the only copy of
anything.** The audit confirmed the repo already honours this: rate-limit
counters, the leader-election lock, and the cache block all treat Redis as a
speed/coordination layer with a working in-memory fallback. Any new work
inherits the same law.

## Headline finding: 3 of 5 phases are already partly-to-mostly built

| Phase | Generic spec | Fork reality today | Verdict |
|---|---|---|---|
| **P1 Optional client + graceful degradation** | build an optional Redis client, ping-gated, never raises; health surfaces it | **Partial.** `hydration_scheduler.py`, `rate_limit.py`, `session_store.py`, `blocks/cache_manager.py` each open their OWN `REDIS_URL` client with their own fallback. No single shared client; `/v1/health` does NOT surface redis. `redis==8.0.0` already pinned. | **Consolidate, don't create.** |
| **P2 Distributed rate limiting** | Redis sliding-window limiter, shared across workers | **DONE.** `app/core/rate_limit.py` — Redis sorted-set sliding window via Lua, in-process fallback, wired in `main.py` (`init_rate_limiter()` + `rate_limit_middleware`), `RATE_LIMIT_PER_MINUTE`. | **Ship-as-is; add tests only.** |
| **P3 arq job queue for ingestion** | move Drive-folder import + eager indexing to arq workers | **Not built.** Ingestion runs via FastAPI `BackgroundTasks` inline (`drive.py _run_index_folder_bg`, `doc_index.maybe_eager_index`). `arq` is NOT in requirements. `entrypoint.sh` already supports `WORKER_COMMAND` (the worker hook exists). | **Real net-new work — the only substantial phase.** |
| **P4 Conservative caching (retrieval BANNED)** | short-TTL cache for cheap derived reads; never cache retrieval | **Partial.** `blocks/cache_manager.py` is a generic Redis-or-memory KV block, but it is a *block* invoked explicitly, not a caching layer wired into any read path. Retrieval is currently uncached (compliant by default). | **Thin wrapper + the explicit ban test; low effort.** |
| **P5 Multi-worker unlock** | raise `UVICORN_WORKERS` once state is shared | **Gated correctly.** `scripts/uvicorn_worker_count.sh` = `UVICORN_WORKERS` (default 1). Blockers today: session store + rate limit already share via Redis, but **in-memory OAuth `_pending_states` is fixed (signed stateless, #276)** and **`_DRIVE_FOLDER_JOBS` is still process-local** (P3 fixes this). | **Flip last, after P3.** |

**Net:** the program is really **~1.5 phases of new code** (P3, plus small P1
consolidation and a P4 wrapper), not five. P2 is done; P5 is a one-line env flip.

## Fork-specific tailoring of each phase

### P1 — one shared client (consolidation, ~S)
- New `app/core/redis_client.py`: `get_redis()` → cached, ping-gated, returns
  `None` when `REDIS_URL` unset/unreachable; never raises. `redis_available()`.
- Refactor the four existing call sites to use it (keep their fallbacks).
- `/v1/health` gains `redis: {configured, connected}` (mirrors the existing
  `sentry_enabled` pattern in `health.py`).
- **Sovereignty:** Redis is a LOCAL container (see P-compose). It is NOT egress
  — it never leaves the box. `deployment_profile.onprem` needs no
  `forbid_onprem` guard on Redis. **Action:** add one row to the EGRESS ledger
  explicitly recording "Redis = localhost/compose service, zero external
  egress" so the sovereignty posture is documented, not just true.

### P2 — already done (tests only, ~S)
- Add the doctrine test shapes it lacks: rate-limit **matrix** (identity ×
  under/over limit), and an **outage-injection** test (Redis dies mid-window →
  limiter falls back to in-process, no 500s). No product change.

### P3 — arq ingestion queue (the real build, ~L)
- Add `arq` to requirements. New `app/core/job_queue.py`: enqueue via arq when
  Redis present, else run inline through today's `BackgroundTasks` path
  (identical behaviour — this is the fallback that keeps single-box/dev/onprem
  working with zero Redis).
- Move `_run_index_folder_bg` + `maybe_eager_index` behind the queue. **Job
  STATUS moves to Redis (ephemeral, re-runnable); files + chunks stay
  Postgres/disk (truth).** `_DRIVE_FOLDER_JOBS` process-local dict retires.
- render.yaml: a background-worker service running the arq worker via the
  existing `WORKER_COMMAND` hook.
- **Doctrine tests:** conservation (`N` enqueued → executed **exactly once**);
  flush-Redis mid-run → **zero data loss** (files/chunks intact, job re-runs);
  Redis-absent → inline path byte-identical to today.

### P4 — conservative caching (~S)
- Thin `cache_get/cache_set(ttl)` on the P1 client for 1–2 **cheap-to-rebuild
  derived reads only** (candidates: `/v1/health` block census, project
  document-count badges). Short TTL (≤60s).
- **Hard ban, pinned by test:** `retrieve_with_filter`, `search_project_documents`,
  and the chat paths must NEVER read or write cache. A test greps/asserts the
  retrieval + chat modules import no cache helper; a flush test asserts a
  grounded answer is byte-identical cached vs uncached.

### P5 — multi-worker unlock (~XS, last)
- After P3 lands, raise `UVICORN_WORKERS` (env only, no code). Pre-flip audit:
  confirm no remaining process-local mutable state on a request path (the
  known two — OAuth states, folder-job dict — are both resolved by then).

## Sovereignty / fork-in-a-box impact (per the standing on-prem law)

- Redis ships as a **compose service** (uncomment the block already stubbed in
  `docker-compose.yml` lines 48–56 + the `redis_data` volume). Local only.
- **Boot-test both ways** (this is the acceptance gate, per your instruction):
  fork-in-a-box up **with** the redis container (arq worker active, distributed
  limiter) and **without** it (every path falls back: inline ingestion,
  in-process limiter, no cache). Both must reach a healthy `/v1/health`.
- **No new egress.** Redis is localhost. EGRESS ledger gets the one documenting
  row; SOVEREIGNTY_REPORT re-checked — posture unchanged.

## Recommended sequence (matches your "one infra change at a time, smoke-gated")

1. **P1** consolidate client + health + EGRESS row → smoke (`consistency_oracle.py`).
2. **P2** tests only (no risk).
3. **P4** cache wrapper + ban test → smoke.
4. **P3** arq queue (the real change) → fork-in-a-box boot-test with+without
   redis → smoke → re-run 4-bug re-test surfaces (upload path is P3-adjacent).
5. **P5** flip `UVICORN_WORKERS` → smoke → hands-on.

Each step is independently revertible (all Redis paths degrade to today's
in-memory behaviour when `REDIS_URL` is unset), so a regression names its own
cause. Update `docs/PROGRESS.md` + a DECISIONS entry after P1 and after P3.

## One correction to the generic spec

The generic program treats P1 (client) and P2 (rate limiting) as net-new. In
the Fork they are **consolidation + done** respectively. Building them fresh
would DUPLICATE `rate_limit.py` and the four existing Redis clients — the
opposite of the boundary-law intent. The Fork delta is **P3 + glue**, and the
plan above is scoped to that reality.
