# Session Reference

A pointer for picking this work back up. Last updated 2026-05-20.

## Where things stand

- **Branch:** `claude/resume-session-5af102cf-uB7bJ`
- **Last commit:** `dd9f750` (pushed to `origin`)
- **Tests:** 396 passing, 90 skipped (full suite, `tests/` minus `tests/browser`).
  Skips are env-gated: Redis tests need `REDIS_URL`, live-LLM tests need
  `DEEPSEEK_API_KEY`.

## Two workstreams

### 1. Roadmap V2 (project mode + conversational platform) — see `ROADMAP_V2.md`

Part 0 + Epics 1–7 implemented, tested, pushed. Follow-ups now also done:
- **Epic 4 Slice B** — conversational-first UI (`app/static/index.html`):
  sidebar wired to real `/v1/projects`, on-demand artifacts panel.
- **Epic 5 leftover** — redline/markup detection (`app/core/redline.py`,
  HSV colour-channel analysis, wired into OCR).
- **Encryption at rest** — `app/core/file_crypto.py`, opt-in via
  `DATA_ENCRYPTION_KEY` (see `DATA_GOVERNANCE.md`).

Only remaining: **Aconex real OAuth connector** — blocked on Aconex API
credentials.

### 2. Reasoning Engine — see `docs/superpowers/plans/2026-05-20-reasoning-engine-INDEX.md`

An AI reasoning layer between the user and the block catalogue
(UNDERSTAND → PLAN → EXECUTE → DELIVER). **All 7 plans (1, 1b, 2–6) are
implemented, tested, committed and pushed.**

- `app/lib/pm_computations.py` — CPM, resource histogram, Gantt, compression,
  `parse_xer`, `write_schedule_excel`.
- `app/core/session_store.py` — swappable in-memory / Redis session state.
- `app/core/sandbox.py` — RestrictedPython jail for generated code.
- `app/blocks/formula_executor_v2.py` — LLM code-gen → sandbox → cache → retry.
- `app/blocks/project_reasoner.py` + `app/core/plan_executor.py` — the agent.
- `app/routers/project.py` — `POST /v1/project/ask`; project-chat mode in
  `app/static/index.html`.

**Pending only on credentials:** live end-to-end LLM tests are written and
`@pytest.mark.skipif`-gated on `DEEPSEEK_API_KEY` (key pending refill). No code
change needed once funded — the tests just stop skipping.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows path
ENV=development PYTHONIOENCODING=utf-8 .venv/Scripts/python -m uvicorn app.main:app
#   → http://localhost:8000/
```

API key for `/v1/*`: `Authorization: Bearer cb_dev_key` (development only).
