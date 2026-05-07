# The Fork

A self-contained fork of Cerebrum Blocks — a FastAPI service that exposes a catalog of AI / document / construction "blocks" through a single universal `/v1/execute` endpoint, plus a small React dashboard for trying them in the browser.

This fork strips out the hosted-platform machinery: no Render deploy, no marketplace/store frontend. Everything runs on your laptop or in a Codespace.

---

## Quickstart

```bash
git clone https://github.com/bopoadz-del/The_Fork.git
cd The_Fork
./start-local.sh
```

Then open:

| URL | What it is |
|-----|------------|
| `http://localhost:8000/`           | Landing page |
| `http://localhost:8000/dashboard/` | React dashboard (block playground) |
| `http://localhost:8000/docs`       | Interactive Swagger UI |
| `http://localhost:8000/v1/health`  | JSON health check |

The launcher will:
1. `npm install` + build the React frontend the first time (only).
2. Source `.env` if present (so `DEEPSEEK_API_KEY` etc. flow through).
3. Start uvicorn on `:8000`.

Useful flags:

```bash
./start-local.sh --rebuild   # force a fresh dashboard build
./start-local.sh --dev       # also start vite dev server on :5173 (HMR)
```

### Run with Docker instead

```bash
docker compose up --build
# → http://localhost:8000
```

---

## Authentication

Every `/v1/*` call needs an API key.

In `ENV=development` (the default for `start-local.sh`), a built-in dev key is enabled:

```bash
curl -H "Authorization: Bearer cb_dev_key" http://localhost:8000/v1/blocks
```

For real keys, set environment variables of the form `CEREBRUM_API_KEY_<NAME>=<key>` in `.env`.

---

## Universal API

Every block speaks the same shape — one endpoint, JSON in, JSON out.

**Single block:**

```bash
curl -X POST http://localhost:8000/v1/execute \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer cb_dev_key" \
  -d '{
    "block": "translate",
    "input": "hello world",
    "params": {"target": "fr"}
  }'
```

**Chain blocks (output of one → input of next):**

```bash
curl -X POST http://localhost:8000/v1/chain \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer cb_dev_key" \
  -d '{
    "steps": [
      {"block": "pdf",  "params": {"extract_text": true}},
      {"block": "chat", "params": {}}
    ],
    "initial_input": {"url": "report.pdf"}
  }'
```

**Discover blocks:**

```bash
curl -H "Authorization: Bearer cb_dev_key" http://localhost:8000/v1/blocks
```

Each block returns a `ui_schema` so the dashboard auto-renders inputs.

---

## Blocks Available in This Fork

The server reports 28 blocks loaded at startup. They fall into four buckets:

### Documents & content (7)
- `pdf`, `pdf_v2` — PDF text & table extraction
- `ocr`, `ocr_v2` — OCR over images (uses Tesseract)
- `image` — Image analysis via Claude Vision (or basic metadata fallback)
- `document_engine` — Parse → Reason → Map pipeline for technical docs
- `web` — Fetch and extract content from any URL

### Language & AI (5)
- `chat` — LLM chat (DeepSeek by default; ANTHROPIC_API_KEY also supported)
- `translate` — 20+ languages, no API key needed
- `voice` — Text-to-speech (gTTS) and speech-to-text (Google STT)
- `vector_search` — In-memory semantic search
- `zvec` — TF-IDF embeddings, similarity, zero-shot classification

### Construction domain (10)
- `construction_v2` — Construction document analysis (typed I/O)
- `boq_processor` — Parse Excel/CSV Bills of Quantities
- `bim`, `bim_extractor` — IFC building elements, quantities, clash report
- `drawing_qto` — Measurements / areas / volumes from DXF/DWG
- `primavera_parser` — Parse P6 `.xer` schedule files
- `spec_analyzer` — Grade requirements, material specs, compliance
- `formula_executor` — Chat-to-code: generate & run Python formulas
- `sympy_reasoning` — Symbolic variance analysis
- `historical_benchmark` — RS Means-style unit costs & market data
- `smart_orchestrator` — Keyword router that maps user messages to the right construction block

### Drives & infrastructure (4)
- `local_drive` — Local filesystem list/read/write
- `google_drive` — Google Drive (needs `GOOGLE_CLIENT_ID` + token)
- `onedrive` — OneDrive (needs `AZURE_CLIENT_ID` + token)
- `cache_manager` — Redis wrapper with get/set/delete/stats

Get the live list at runtime:

```bash
curl -H "Authorization: Bearer cb_dev_key" http://localhost:8000/v1/blocks \
  | jq '.blocks[] | {name, layer, description}'
```

---

## Configuration

Drop a `.env` in the repo root. All keys are optional — blocks that need them will report a clear error if they're missing.

```dotenv
# LLM providers (chat, formula_executor, etc.)
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Drive integrations
GOOGLE_CLIENT_ID=...
GOOGLE_REFRESH_TOKEN=...
AZURE_CLIENT_ID=...
ONEDRIVE_ACCESS_TOKEN=...

# Redis (for cache_manager)
REDIS_URL=redis://localhost:6379

# Misc
DATA_DIR=./data
ENV=development
CORS_EXTRA_ORIGINS=http://localhost:9000   # comma-separated
```

---

## Architecture

```
┌─────────────────────────────────────────┐
│   React dashboard (frontend/dist)       │  served at /dashboard
└─────────────┬───────────────────────────┘
              │ HTTP (CORS open to localhost)
┌─────────────▼───────────────────────────┐
│   FastAPI app (app/main.py)             │
│   /v1/execute, /v1/chain, /v1/blocks ...│
└─────────────┬───────────────────────────┘
              │
        ┌─────┴──────┐
        ▼            ▼
┌──────────────┐  ┌──────────────┐
│ block: pdf   │  │ block: chat  │  ... 28 blocks total
└──────────────┘  └──────────────┘
```

Each block lives in `app/blocks/<name>.py`, inherits from `UniversalBlock`, and implements:

- `process(input_data, params)` — the actual logic
- `execute(input_data, params)` — wrapper added by the base class (timing, error handling, `source_id`)

Routers wire the catalog to HTTP in `app/routers/`.

---

## Repo Layout

```
app/
  main.py             FastAPI app + CORS + /dashboard mount
  blocks/             28 universal blocks
  routers/            HTTP routers (blocks, execute, chain, chat, ...)
  core/               UniversalBlock base, auth, schema registry
  static/             Landing page assets
frontend/             React + Vite dashboard (mounted at /dashboard)
data/                 DATA_DIR — uploads & block state
docker-compose.yml    Single-container setup
start-local.sh        One-command launcher (build + serve)
```

---

## Documentation

- [API.md](API.md) — full endpoint reference
- [API docs (live)](http://localhost:8000/docs) — once the server is running

---

*Fork of [bopoadz-del/Cerebrum-Blocks](https://github.com/bopoadz-del/Cerebrum-Blocks). Trimmed for local use — no hosted platform, no marketplace.*
