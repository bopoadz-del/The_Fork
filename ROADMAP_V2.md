# The Fork — Roadmap V2: Project Mode & Conversational Platform

> Status: proposed · Author: handover from session `5af102cf` · Supersedes the eager
> auto-pipeline behaviour described in `ROADMAP_PATH_C.md`.

This roadmap turns The Fork from an **eager document processor** into a
**project-aware conversational assistant**. Two principles drive every item below:

1. **Nothing runs without user intent.** Attaching a file ≠ asking for analysis.
2. **Work lives inside a Project.** Project-level analytics (progress tracking,
   earned value, risk) are only meaningful once a project is set up and its
   source-of-truth systems (baseline schedule, daily/weekly reports, Aconex) are
   connected. Until then, those features stay *armed but inert*.

---

## Current-state summary (why this roadmap exists)

| Area | Today | Problem |
|------|-------|---------|
| Upload | `/ingest` (`app/routers/upload.py:102`) auto-runs the full document_engine pipeline on every file | Analysis fires on attach, not on request |
| Panels | `renderPanels()` (`app/static/index.html:913`) repaints the right panel on every upload/message | Commands "auto-generate" with no user ask |
| Project tracker | `progress_tracker()` (`app/containers/construction.py:1909`) computes from per-call params | No project context; runs on stray documents |
| Project entity | **Does not exist** — sidebar projects in `index.html:95` are hardcoded décor | No way to group docs or gate features |
| Confidence | `_calculate_confidence()` (`construction.py:496`) returns hardcoded `0.7` | "Trust" indicator is fiction |
| Document types | `_classify_document()` (`construction.py:279`) — filename regex, ~10 hardcoded types | Not extensible |
| Chaining | `/v1/chain` (`app/routers/chain.py:40`) — JSON only | Dev-only, invisible to users |
| Chat UI | 3-column canvas; right panel = results grid | Reads as a dashboard, not a conversation |
| OCR quality | `ocr.py:20` config keys (`deskew`, `contrast_factor`) parsed but **never applied** | Poor scans degrade silently |
| Data governance | Files in `DATA_DIR`, no retention/audit/deletion | No client-facing policy |
| Aconex | No code | Required for a live project tracker |

---

# Part 0 — Behavioural corrections (do first, blocks everything else)

These are not "nice to have" — they are the foundation the seven epics build on.

## 0.1 — Introduce the Project entity

**Why first:** project memory, the readiness gate, per-project governance and the
artifacts panel all need a `Project` to hang off of.

**Scope**
- New SQLite-backed model: `Project { id, name, client, created_at, status,
  connectors[], readiness{} }`. Use SQLite via `aiosqlite` (already on the
  optional dep line) — no external DB.
- New router `app/routers/projects.py`: `POST /v1/projects`, `GET /v1/projects`,
  `GET /v1/projects/{id}`, `POST /v1/projects/{id}/documents`.
- Every uploaded file gets a `project_id` (replaces the flat UUID namespace in
  `upload.py:57`). Documents with no project go to a per-session "scratch" project.
- Wire the real list into `index.html:95` (kill the hardcoded Diriyah/Qiddiya/KAUST).

**Acceptance**
- Creating a project, attaching 3 documents, and reloading shows the same 3
  documents grouped under it.
- `GET /v1/projects/{id}` returns document list + readiness object.

**Effort:** L (3–5 days)

## 0.2 — Project-readiness gate for the tracker

**Why:** "the project tracker should work only once a project is set up — all
documents loaded, baseline + daily/weekly reports in, and Aconex connected — not
on every document attached."

**Scope**
- Add a `readiness` computed object to `Project`:
  ```
  readiness = {
    baseline_schedule: bool,   # an original P6/XER baseline is loaded
    daily_reports:     int,    # count
    weekly_reports:    int,    # count
    aconex_connected:  bool,
    ready:             bool    # all required signals present
  }
  ```
- `progress_tracker()` (`construction.py:1909`) and other project-level actions
  check `project.readiness.ready` first. If not ready, return a structured
  **"not ready"** response listing exactly what is missing — never zeros, never
  fabricated numbers.
- The classifier (`_classify_document():279`) tags baseline vs. revised schedules
  and daily/weekly reports so readiness can be computed automatically.

**Acceptance**
- Calling `progress_tracker` on a project missing the baseline returns
  `status: "not_ready"` with `missing: ["baseline_schedule", "aconex"]` — not the
  all-zeros payload seen today.
- Once baseline + ≥1 daily + ≥1 weekly + Aconex are present, the tracker runs.

**Effort:** M (2–3 days, after 0.1)

## 0.3 — Execution-intent model: stop auto-running panel commands

**Why:** "all these commands that auto-generate on the panel shall only be
executed when the user [asks], and results [appear] as a normal reply in the
chat box."

**Scope**
- Split `/ingest` (`upload.py:102`) into two endpoints:
  - `POST /v1/projects/{id}/documents` — **stores and classifies only.** No
    pipeline. Returns a one-line chat acknowledgement
    (`"Added Schedule_Rev3.pdf — classified as baseline schedule."`).
  - `POST /v1/execute` (existing) — runs analysis **only when the user asks.**
- Frontend: attaching a file posts to the store-only endpoint. The right panel
  no longer repaints on upload. `renderPanels()` is driven by explicit user
  requests, not uploads.
- Analysis results are returned as a **chat message** (conversational reply),
  with any structured output surfaced as an *artifact* in the side panel
  (see Epic 4) — not as an auto-refreshing results grid.

**Acceptance**
- Attaching 5 documents triggers **zero** block executions (verify via
  monitoring block / logs).
- Asking "give me the BOQ summary" runs exactly one pipeline and the answer
  appears as a chat reply.

**Effort:** M (2–3 days)

---

# Part 1 — The seven epics

Each epic: **Problem → Goal → Approach → Key files → Acceptance → Effort.**

## Epic 1 — Measured confidence scores

**Problem:** the "trust/check" indicator is `return {"overall": 0.7}`
(`construction.py:496`). It is a constant, not a measurement.

**Goal:** replace it with real, per-result **accuracy / error metrics** that a QS
or PM can act on.

**Approach**
- Define a `ConfidenceReport` model (extend `app/core/panels.py`):
  ```
  { extraction_recall, field_coverage, ocr_char_confidence,
    cross_check_agreement, source_pages, caveats[] }
  ```
- Derive each signal from real data:
  - **OCR char confidence** — from Tesseract `image_to_data` (currently
    discarded; see Epic 5).
  - **Field coverage** — % of expected fields actually populated for the
    detected document type.
  - **Cross-check agreement** — when two extractors see the same value (e.g.
    BOQ total vs. sum of line items), report the delta.
  - **Caveats** — explicit strings ("3 pages were scanned and OCR'd",
    "no baseline to compare against").
- Delete the unreachable second `_calculate_confidence` at `construction.py:2755`.
- UI: replace the single badge with a small **metrics breakdown** on the
  artifact card — show the numbers, not a vibe.

**Key files:** `construction.py:496,2755`, `app/core/panels.py`,
`app/blocks/ocr*.py`, `document_engine.py`.

**Acceptance**
- A clean digital PDF and a poor scan of the same document produce *visibly
  different* confidence reports.
- No code path returns a hardcoded confidence constant.

**Effort:** L (4–6 days; depends on Epic 5 for OCR signal)

## Epic 2 — Support for custom document types

**Problem:** types are hardcoded filename regex in `_classify_document()`
(`construction.py:279`); adding one means editing core code.

**Goal:** users (power users / admins) can register a new document type without a
code change.

**Approach**
- Move type definitions into a registry: `config/document_types.yaml` — each entry
  has `name`, `match` (filename + content keyword rules), `extractor`, `expected_fields`.
- `_classify_document()` reads the registry; built-ins (drawing, P6/XER schedule,
  BOQ, contract, spec, photo) ship as the default file.
- Add **content-based** detection (first N pages keyword scan), not filename-only.
- New `POST /v1/projects/{id}/document-types` (admin/power-user) to add a type
  with a generic extractor + field schema.
- Unknown documents are classified `"unrecognised"` and the user is *asked*
  rather than silently defaulted to `"drawing"`.

**Key files:** `construction.py:232–299`, new `config/document_types.yaml`,
`document_engine.py`.

**Acceptance**
- Adding a "Method Statement" type via config (no redeploy of core) routes a
  matching upload correctly.
- An unrecognised file prompts the user instead of guessing.

**Effort:** L (4–6 days)

## Epic 3 — Project memory

**Problem:** every document is processed in isolation; nothing accumulates.

**Goal:** knowledge **accumulates across documents in the same project** — later
questions use earlier documents.

**Approach**
- Per-project store (built on Epic 0.1): structured facts (`project_facts`
  table) + a vector index for semantic recall. Use the existing optional
  `chromadb` dep, or a lightweight numpy cosine index to stay within the
  512 MB free-tier budget (`BRANCH.md`).
- On document analysis, extract durable facts (contract value, key dates,
  parties, BOQ totals, baseline milestones) and write them to project memory.
- Chat (`app/routers/chat.py`) retrieves relevant project facts/snippets and
  injects them as context — replacing today's current-turn-only file context
  (`index.html:1068`).
- Memory is **scoped to one project**; no cross-project leakage (governance).
- A "Project Knowledge" artifact lets the user inspect and correct stored facts.

**Key files:** `chat.py:103–193`, new `app/core/project_memory.py`,
`construction.py` extractors.

**Acceptance**
- Upload a contract, then later ask "what's the LD rate?" in a new chat turn —
  answered from memory without re-attaching the contract.
- Facts are listed per project and editable.

**Effort:** XL (6–10 days; depends on 0.1)

## Epic 4 — Conversational UI + Claude-style artifacts panel

**Problem:** the current 3-column canvas reads as a **results dashboard**. The
right panel auto-fills with grids. It is not a chatbot.

**Goal:** a genuine **conversational chatbot** where the left/side panel behaves
like Claude's artifacts panel — it shows links, code, files, artifacts,
"code vibing" — *contextually, driven by the chat request and content type*.

**Approach**
- **Conversation-first layout:** the chat thread is the primary surface.
  Messages, multi-turn, streaming (already in `chat.py:103`). Remove the
  always-on results grid.
- **Artifacts panel:** a side panel that opens *on demand* when a reply produces
  something inspectable:
  - tables/quantities → interactive table artifact
  - schedules → Gantt/timeline artifact
  - code/JSON → syntax-highlighted code artifact
  - files/drawings → file preview artifact
  - links → link cards
  The panel is empty until a reply yields an artifact — it never auto-populates
  on upload.
- Define an **artifact contract** (extend `app/core/panels.py`): every block
  result optionally carries `artifacts: [{ type, title, payload }]`. The chat
  reply is prose; artifacts are attachments to it.
- Rebuild on the React stack (`frontend/src/`) and retire the static
  `app/static/index.html` canvas, or port the canvas to the artifact model —
  decide in a spike (see Open Questions).

**Key files:** `frontend/src/blocks/Chat/index.tsx`, `frontend/src/pages/`,
`app/static/index.html` (retire), `app/core/panels.py`, `chat.py`.

**Acceptance**
- A plain question gets a plain chat reply, no panel.
- "Extract the BOQ" gets a prose reply **and** a table artifact in the side panel.
- Attaching a file opens no panel and runs nothing (ties to Epic 0.3).

**Effort:** XL (8–12 days)

## Epic 5 — Input quality handling

**Problem:** OCR preprocessing config (`deskew`, `contrast_factor`,
`preprocess`) exists at `ocr.py:20` but is **never applied**. Poor scans and
marked-up prints degrade silently.

**Goal:** robust handling of poor scans, redlined/marked-up prints and messy PDFs,
with the quality surfaced to the user.

**Approach**
- Actually implement the preprocessing pipeline (Pillow / OpenCV-lite):
  deskew, denoise, contrast/threshold, DPI normalisation — applied before
  Tesseract.
- Capture **per-word OCR confidence** from `pytesseract.image_to_data` and feed
  it into Epic 1.
- Detect markup/redlines (colour-channel analysis) and flag annotated regions
  rather than mangling them into the text.
- Quality gate: if mean OCR confidence < threshold, the chat reply *says so*
  ("This scan is low quality — extracted text may be unreliable") instead of
  presenting it as clean data.
- Make the OCR-vs-text-extraction choice **visible** (today it is a silent
  fallback in `document_engine.py`).

**Key files:** `app/blocks/ocr.py`, `app/blocks/ocr_v2.py`,
`app/blocks/document_engine.py`.

**Acceptance**
- A skewed, low-contrast scan produces materially better text after
  preprocessing (measured by char confidence).
- Low-quality input yields an explicit caveat in the reply.

**Effort:** L (4–6 days)

## Epic 6 — Data governance transparency

**Problem:** no retention policy, no audit log, no deletion endpoint, no
documented confidentiality stance (`upload.py:22–28`).

**Goal:** clear, documented, enforced policy for client documents.

**Approach**
- Write `DATA_GOVERNANCE.md`: where data lives, retention window, encryption
  posture, who can access, deletion-on-request — and reflect it honestly
  (don't claim encryption that isn't there).
- Implement what the doc promises:
  - `DELETE /v1/projects/{id}` and `DELETE .../documents/{doc_id}` that purge
    files + memory + cache.
  - Configurable retention (`DATA_RETENTION_DAYS`) with a cleanup job.
  - Append-only audit log (upload, access, delete) per project.
  - At-rest encryption option for `DATA_DIR` (the `cryptography` dep is already
    installed).
- Surface a per-project "Data & Privacy" artifact: what is stored, when it
  expires, a delete button.

**Key files:** `app/routers/upload.py`, new `app/routers/projects.py`,
new `app/core/audit.py`, new `DATA_GOVERNANCE.md`.

**Acceptance**
- Deleting a project removes every trace (files, memory, cache, audit closes).
- `DATA_GOVERNANCE.md` matches actual behaviour — verified by test.

**Effort:** M–L (3–6 days; depends on 0.1)

## Epic 7 — Expose custom chaining to power users

**Problem:** `/v1/chain` (`chain.py:40`) + the orchestrator
(`app/blocks/orchestrator.py:53`) work but are **JSON-only, dev-only**.

**Goal:** a **power-user mode** that exposes chaining without requiring API
knowledge.

**Approach**
- A **Power User toggle** in settings (gated by role — `auth` block already has
  roles: admin/pro/basic).
- A visual chain builder in React: pick blocks, see input/output types, the
  `DataTransformer` (`orchestrator.py`) shows auto-conversions inline.
- **Save / name / re-run** chains as project-scoped workflows (fills the
  "stateless, no persistent workflows" gap).
- Step-by-step execution with per-step output inspection (debug mode).
- Saved chains become invokable from chat ("run my Tender Review workflow").
- Keep the JSON API as the advanced escape hatch.

**Key files:** `app/routers/chain.py`, `app/blocks/orchestrator.py`,
`app/blocks/smart_orchestrator.py`, `frontend/src/` (new builder),
new `workflows` table.

**Acceptance**
- A pro-role user builds, names, saves and re-runs a 3-step chain entirely in
  the UI.
- A saved workflow is invokable by name from chat.

**Effort:** XL (8–12 days)

---

## Cross-cutting prerequisite — Aconex connector

The project tracker is only "live" once Aconex is connected (Epic 0.2). This is
its own work item, not in the seven but required by them:

- OAuth2 client for Oracle Aconex; store tokens per project (encrypted, Epic 6).
- Pull registers: documents, mail, RFIs, the project schedule.
- Connection status feeds `project.readiness.aconex_connected`.
- New `app/routers/connectors.py` + `app/blocks/aconex.py`.

**Effort:** XL (8–12 days) — schedule alongside Phase 2.

---

## Sequencing

```
Phase 1  Foundation        0.1 Project entity → 0.2 Readiness gate → 0.3 Intent model
Phase 2  Trust & inputs    Epic 5 (OCR quality) → Epic 1 (confidence)   + Aconex connector
Phase 3  Knowledge         Epic 3 (project memory)
Phase 4  Experience        Epic 4 (conversational UI + artifacts)
Phase 5  Power & extend    Epic 2 (custom doc types) ∥ Epic 7 (chaining UI)
Phase 6  Compliance        Epic 6 (data governance)
```

Rationale: **Phase 1 is non-negotiable first** — every other epic assumes a
Project exists and that nothing auto-runs. Epic 5 precedes Epic 1 because real
confidence needs real OCR signal. Epic 4 lands after memory so the artifacts
panel has something worth showing. Epic 6 can slip late but `DATA_GOVERNANCE.md`
(the document) should be drafted in Phase 1.

## Milestone table

| Phase | Deliverable | Effort | Gate |
|-------|-------------|--------|------|
| 1 | Projects exist; tracker gated; uploads run nothing | ~7–11 d | No execution on attach (verified) |
| 2 | Real OCR preprocessing + measured confidence; Aconex live | ~16–24 d | Confidence varies with input quality |
| 3 | Cross-document project memory | ~6–10 d | Recall a fact without re-attaching |
| 4 | Conversational UI + on-demand artifacts panel | ~8–12 d | Chat reads as a chatbot, not a dashboard |
| 5 | Custom document types + visual chain builder | ~12–18 d | Add a type / save a workflow with no code |
| 6 | Enforced, documented data governance | ~3–6 d | Delete-project purges everything |

## Open questions

1. **React vs. static canvas (Epic 4):** port `app/static/index.html` to the
   artifact model, or rebuild fully in `frontend/src/`? Recommend a 1-day spike.
2. **Persistence on free tier:** SQLite on the 512 MB Render free tier has no
   persistent disk (`BRANCH.md`). Project mode likely needs a small persistent
   volume or an external DB — decide before Phase 1.
3. **Aconex API access:** confirm the client has Aconex API credentials and the
   required entitlements before scheduling that connector.
4. **Confidence ground truth (Epic 1):** is there a labelled document set to
   calibrate against, or is confidence purely self-reported signal?

---

*Generated as a handover artifact. The two behavioural corrections (Part 0) are
the immediate priority — they are what makes the platform stop "auto-generating
on the panel" and start behaving like a conversational assistant.*
