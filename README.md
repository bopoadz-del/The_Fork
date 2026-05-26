# The Fork

A self-contained fork of Cerebrum Blocks — a FastAPI service that exposes a catalog of AI, document, and construction "blocks" through a single universal `/v1/execute` endpoint, plus a chat UI for trying them in the browser. Runs locally; no hosted platform.

**Live:** http://localhost:8000/

---

## Quickstart

```bash
git clone git@github.com:bopoadz-del/The_Fork.git
cd The_Fork
./start-local.sh
```

Then open:

| URL | What it is |
|-----|------------|
| `http://localhost:8000/`           | Chat UI (landing page) |
| `http://localhost:8000/docs`       | Interactive Swagger UI |
| `http://localhost:8000/v1/health`  | JSON health check |

The launcher sources `.env` if present (so `DEEPSEEK_API_KEY` etc. are picked up), then starts uvicorn on `:8000`.

### Run with Docker instead

```bash
docker compose up --build
# → http://localhost:8000
```

A prebuilt image is also published on each push to main:

```bash
docker pull ghcr.io/bopoadz-del/cerebrum-blocks:latest
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

Each block returns a `ui_schema` so the UI auto-renders inputs.

---

## Blocks

35 blocks reachable via `/v1/blocks`, grouped roughly into:

- **Documents & content** — `pdf`, `pdf_v2`, `ocr`, `ocr_v2`, `image`, `document_engine`, `web`
- **Language & AI** — `chat`, `translate`, `voice`, `vector_search`, `zvec`
- **Construction domain** — `construction_v2`, `boq_processor`, `bim`, `bim_extractor`, `drawing_qto`, `primavera_parser`, `spec_analyzer`, `formula_executor`, `sympy_reasoning`, `smart_orchestrator`
- **Drives & infrastructure** — `local_drive`, `google_drive`, `onedrive`, `cache_manager`

Live list with descriptions:

```bash
curl -H "Authorization: Bearer cb_dev_key" http://localhost:8000/v1/blocks \
  | jq '.blocks[] | {name, layer, description}'
```

---

## Configuration

Drop a `.env` in the repo root. All keys are optional — blocks that need them will report a clear error if they're missing. See `.env.example` for the full template.

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
│   Chat UI (app/static/index.html)       │  served at /
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
│ block: pdf   │  │ block: chat  │  … 35 blocks
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
  main.py             FastAPI app, CORS, mounts
  blocks/             All universal blocks
  routers/            HTTP routers (blocks, execute, chain, chat, …)
  core/               UniversalBlock base, auth, schema registry
  static/             Chat UI (index.html + assets)
data/                  DATA_DIR — uploads & block state
docker-compose.yml     Single-container setup
start-local.sh         One-command launcher
```

---

## Documentation

- [API.md](API.md) — full endpoint reference
- [API docs (live)](http://localhost:8000/docs) — once the server is running

---

*Fork of [bopoadz-del/Cerebrum-Blocks](https://github.com/bopoadz-del/Cerebrum-Blocks). Trimmed for local use.*
