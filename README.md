# The Fork

A construction-intelligence platform: FastAPI backend + React frontend +
Postgres with pgvector + hybrid retrieval (BM25 + vector RRF) + an
agent runtime that handles project Q&A, BOQ extraction, drawing QTO,
WBS generation, and cost analysis.

**Live:** [the-fork.onrender.com](https://the-fork.onrender.com)

The deployed instance runs on Render (FastAPI web service + Postgres
16 with pgvector). Chat routes through Ollama Cloud via a cloudflared
tunnel. ~142k indexed chunks across two corpus projects
(`training_material` + `projects_folder`).

---

## What it does

The Fork takes a construction project (RFP, BOD, drawings, BOQ, specs,
schedules, reports) and gives the operator a chat surface that:

- Answers document-grounded questions by retrieving from the project
  corpus and citing the source
- Extracts structured Bill of Quantities from PDF / XLSX / CSV
- Runs quantity takeoff on drawings (PDF + DXF)
- Generates CPM-validated work breakdown structures (200+ activities,
  full ES/EF/LS/LF, critical path)
- Reconciles drawing quantities against BOQ totals (variance math via
  sympy) and produces recommendations
- Cites real documents with confidence scores in every answer

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  React frontend (frontend/src/)                                   │
│  - 3-column workspace (left sidebar + chat + right sources)       │
│  - SSE chat streaming with theme + dark mode                      │
└───────────────────────────────┬──────────────────────────────────┘
                                │  /v1/agents/project-assistant/chat
┌───────────────────────────────▼──────────────────────────────────┐
│  FastAPI backend (app/)                                           │
│  - 41 blocks under app/blocks/ (boq_processor, drawing_qto, …)    │
│  - Agent runtime (app/agents/) with smart_orchestrator routing    │
│  - Hybrid retriever (BM25 + vector RRF) over the chunks table     │
│  - JWT auth + admin-gated /v1/admin/* endpoints                   │
└─────┬─────────────────────────┬──────────────────────────────────┘
      │                         │
      │  pgvector(256)          │  cloudflared tunnel
      │  + tsvector GIN         │
      ▼                         ▼
┌──────────────┐         ┌──────────────────┐
│  Postgres 16 │         │  Ollama Cloud    │
│  + pgvector  │         │  qwen3-coder:    │
│  on Render   │         │  480b-cloud      │
└──────────────┘         └──────────────────┘
```

Storage:
- **Postgres** — users, projects, documents, conversations, messages,
  chunks (text + embedding + tsvector), agent_facts, hydration_runs.
  15 GB disk on Render basic_256mb plan, daily backups, 7-day
  retention. See [docs/backup-and-recovery.md](docs/backup-and-recovery.md).
- **Render persistent disk** (`/app/data`) — uploaded source documents,
  audit log, evidence vault, learning engine state, Google Drive OAuth
  refresh tokens, `.secret_key` fallback. Daily snapshots, 7-day
  retention.

Retrieval:
- Hybrid: 50 semantic + 50 BM25 candidates fused via Reciprocal Rank
  Fusion. `app/core/rag/retriever.py`.
- Embeddings: model2vec / potion-base-8M, 256-dim. Matches the
  Postgres `chunks.embedding vector(256)` column.
- RAG injection gate: only the `project-assistant` agent gets
  per-turn RAG context (`app/core/rag/inject.py`). Confidence
  threshold 0.4, daily token budget 500K, fallback prefix on miss.

---

## Quickstart

Local development:

```bash
git clone git@github.com:bopoadz-del/The_Fork.git
cd The_Fork
python -m venv .venv
.venv/Scripts/activate          # or: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env             # then fill in the required vars
                                 # SECRET_KEY at minimum

uvicorn app.main:app --reload
```

Visit `http://localhost:8000` for the chat UI.

For production deploy, see [docs/PILOT.md](deploy/PILOT.md) and
[docs/backup-and-recovery.md](docs/backup-and-recovery.md).

---

## API surface

The platform exposes:

- **Chat** — `POST /v1/agents/{agent}/chat` (and `/chat/stream` for SSE)
- **Projects** — `POST/GET/DELETE /v1/projects` and per-project
  `/documents`, `/conversations`, `/memory`, `/audit`
- **Document operations** — `/v1/projects/{id}/documents/search`,
  per-doc redline, exports
- **Drive integration** — `/v1/projects/{id}/drive/index-folder`,
  `/drive/import`
- **Admin (gated)** — `/v1/admin/debug/*`, `/v1/admin/corpus/collections`,
  `/v1/admin/corpus/bulk-insert`, `/v1/admin/training/*`
- **Observability** — `/metrics` (Prometheus, unauth, narrow
  request-counter set), `/v1/metrics` (admin, per-block execution
  data), `/health`

---

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

Coverage floor enforced in CI at 25 % (`.github/workflows/test.yml`).
The `diff-cover` gate ratchets newly-added code upward.

---

## Key references

- [.env.example](.env.example) — every environment variable with
  REQUIRED-PROD / RECOMMENDED / OPTIONAL labels
- [docs/backup-and-recovery.md](docs/backup-and-recovery.md) — backup
  posture + restore procedure for Postgres + disk
- [deploy/PILOT.md](deploy/PILOT.md) — deployment runbook
