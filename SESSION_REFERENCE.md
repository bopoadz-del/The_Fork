# Session Reference — Roadmap V2

A pointer for picking this work back up. Last updated 2026-05-20.

## Where things stand

- **Branch:** `claude/resume-session-5af102cf-uB7bJ`
- **Last commit:** `5327060` — "Roadmap V2: project mode + conversational platform foundation" (pushed to `origin`)
- **Status:** `ROADMAP_V2.md` Part 0 + Epics 1–7 fully implemented, tested, pushed.
- **Tests:** 253 passing (240 core + 13 browser); 37/37 live e2e.

## Resume the work (any machine)

```bash
git clone -b claude/resume-session-5af102cf-uB7bJ git@github.com:bopoadz-del/The_Fork.git
cd The_Fork
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows path
./start-local.sh                                          # → http://localhost:8000/
```

API key for `/v1/*`: `Authorization: Bearer cb_dev_key` (development only).

## Resume the Claude Code conversation (this machine)

```
cd C:\Users\shimm
claude --resume ea7f6afd-05ca-4f7f-9d98-4e7201465583
```
(or `claude --resume` and pick from the list)

## What's done

| Item | State |
|------|-------|
| Part 0 — Project entity, readiness gate, execution-intent | ✅ |
| Epic 1 — Measured confidence | ✅ |
| Epic 2 — Custom document types | ✅ |
| Epic 3 — Project memory | ✅ |
| Epic 4 — Artifact contract + panel (Slice A) | ✅ |
| Epic 5 — Deskew + real OCR confidence | ✅ |
| Epic 6 — Data governance / audit | ✅ |
| Epic 7 — Saved workflows | ✅ |

## What's NOT done (next session starts here)

1. **Epic 4 Slice B** — conversational UI polish: wire the `index.html` sidebar
   to real `/v1/projects`, make chat project-scoped in the UI, trim the
   dashboard-grid feel. Pure frontend (`app/static/index.html`).
2. **Epic 5 leftover** — redline/markup detection (colour-channel analysis).
3. **Encryption at rest** — flagged in `DATA_GOVERNANCE.md`; `cryptography` dep
   is already installed.
4. **Aconex real OAuth connector** — blocked on Aconex API credentials.

## To continue, say

> "Continue the roadmap — Epic 4 Slice B" (or whichever item above).

See `ROADMAP_V2.md` for the full plan and `DATA_GOVERNANCE.md` for the data policy.
