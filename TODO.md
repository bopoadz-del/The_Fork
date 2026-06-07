# TODO

State of the platform after the second session on 2026-06-07.
Live at https://the-fork.onrender.com (auto-deploy from `main`).

## 2026-06-07 session additions (verified end-to-end against live Groq)

- [x] **Soft daily cap on LLM cost** — `runtime.py::_call_llm` now short-circuits with a structured error when today's spend meets `USAGE_DAILY_CAP_USD` for the authenticated user. Internal calls without a user_id are exempt; cap parses to a positive float or check is disabled; tracker failures fail open. 9 unit tests pass. Commit `04e378c`.
- [x] **chat.py block migrated off hardcoded DeepSeek** onto `_llm_config()`. Same provider precedence as the agent runtime; OAI-shape protocol; model placeholder remapped when Groq is active. Stream + non-stream + offline_template all provider-agnostic. Commit `c5d5704`.
- [x] **Cerebrum EVM system prompt landed** at `app/prompts/construction_evm.md` (393 lines: EVM formulas, CBS, traffic lights, variance types, response template). Commit `c5d5704`.
- [x] **ChatBlock accepts system_prompt / system_prompt_file params** — literal string or filename inside `app/prompts/`. Path-traversal hardened. Three call paths (cloud / Ollama / llama.cpp) prepend the system role when resolved. Commit `3220a3d`.
- [x] **ConstructionContainer.chat() route** — delegates to ChatBlock with `system_prompt_file="construction_evm.md"` injected by default. Caller wins if they supply their own prompt. Exposed via `route()` and `get_actions()` so the orchestrator can dispatch with `{"action": "chat"}`. Commit `3220a3d`.
- [x] **End-to-end EVM verification** against live Groq llama-3.3-70b — CPI=0.87, SPI=0.91, BAC=$50M, AC=$18M, EV=$15.6M returned the exact template with correct math (EAC=$57.5M, VAC=-$7.5M, Optimistic $52.6M, Pessimistic $62.5M). 4,073 tokens. All 8 shape checks pass.

### Local commits not yet pushed to GitHub

The 2026-06-07 work is committed locally but not yet pushed (auto-mode classifier flagged direct main push given the "batch fixes into one PR" preference). Push when ready: `04e378c`, `c5d5704`, `3220a3d`.

Format: `[ ]` open, `[x]` done, `[~]` partial / needs verification.


## Verified working tonight (no follow-up needed unless they regress)

- [x] Login + JWT (`tf_token` in localStorage; bootstrap user `shadido.dxb@gmail.com`)
- [x] Project create + workspace
- [x] Composer Attach upload — POST /v1/projects/{id}/documents via the same code path the button hits; HTTP 201; doc encrypted at rest. Used during the Edge run with the real Anthropic RFP (24 KB) and a 65-byte smoke file.
- [x] Google Drive OAuth + walker — `/v1/projects/{id}/drive/index-folder` pulled 5 real files from the operator's Drive (PRC-201, PRC-301, etc.) into project `fb776aa2`. Native Google Docs / Sheets / Slides export through `/files/{id}/export?mimeType=`.
- [x] Heavy-reasoning agent on Groq llama-4-scout-17b — tool-calling, validation field on tool envelopes.
- [x] generate_wbs end-to-end — 127 activities, 1352 days, 11 phases from the Anthropic RFP brief.
- [x] Schedule exports xlsx / docx / pdf — all three returned valid bytes with correct MIME headers, 13 KB / 40 KB / 16 KB.
- [x] Usage tracker — `/v1/usage` and `/v1/usage/today` returning real per-user, per-agent, per-provider rows. 41,012 tokens recorded for the Edge run.
- [x] zvec real semantic embeddings (model2vec, 256-dim, related pairs ~0.72 vs unrelated ~0.02).
- [x] validation_pipeline runnable — catches 5,900 degC empirically, 16-week procurement vs 8-week site operationally.
- [x] Decryption transparent across the 11 file-reading blocks (boq, drawing, spec, primavera, pdf, ocr, ocr_v2, document_engine, bim, bim_extractor, image).
- [x] BOQ column aliasing — Rate (SAR) / Amount (SAR) / Qty. all resolve.
- [x] PDF drawing extraction via PyMuPDF vector paths (vector PDFs only).
- [x] Render deploy stable; 44/45 blocks load; auto-validation block included.


## Untested in the browser tonight (code wired, dialog never driven)

- [ ] **Composer file picker dialog** — clicking the `Attach` button in the real UI to open the OS picker. The browser bridge can't drive native dialogs; the fetch code path that fires after picker selection IS verified. Human-click test still owed.
- [ ] **Composer camera capture** — `<input type=file accept="image/*" capture="environment">`. Same situation.
- [ ] **Composer voice recording** — `MediaRecorder` + mic-permission prompt. Same.


## Half-shipped tonight (code in, missing the last small step)

- [ ] **Soft daily cap on LLM cost** — `usage_tracker.is_over_cap(user_id, cap_usd)` exists; the runtime `_call_llm` short-circuit before the HTTP call is NOT wired. ~10 lines in `app/agents/runtime.py::_call_llm`.
- [~] **Tinker training pipeline** — `scripts/run_tinker_training.py` ships with `--dry-run` as default; `--execute` path is wired and syntactically correct but never fired against the live SDK to produce a real LoRA adapter.
- [~] **DWG -> DXF in the live image** — Dockerfile installs `ODAFileConverter` + sets `QT_QPA_PLATFORM=offscreen`. The `drawing_qto._try_convert_dwg` helper looks for it. Never uploaded a real DWG to the live deploy to confirm the end-to-end conversion path works post-ODA bundle.
- [~] **Auto-validation through SSE** — `_collect_numerics` unwrap fix shipped (commit `364663d`). The non-streaming `chat()` is verified locally. The streaming `chat_stream()` SSE event shape that the React UI consumes was not re-verified after the unwrap fix.


## Known gaps / architectural items deferred deliberately

- [ ] **Claude / Kimi-style artifacts side panel** — the current rail shows project metadata + documents but no live preview of generated WBS / exports / code blocks. Needs a real design pass plus a new SSE event richer than today's 400-char `summary` so the React side can render a structured artifact card.
- [ ] **chat.py block still hardcodes DeepSeek** — used by `/v1/blocks/chat/execute` and its streaming generator. Larger surface than the `_llm_config()` migration I did for `runtime.py` / `formula_executor_v2` / `project_reasoner`. Not blocking the agent path, but breaks the `LLM_PROVIDER=groq` promise for the chat-block route.
- [ ] **Heavy-reasoning prompt stored in code** — `app/agents/configs/heavy-reasoning.md` is committed Markdown. Should be DB-backed and editable per project so a Riyadh BIM project and a Houston solar farm can carry different rule sets without a redeploy.
- [ ] **FX rates inside `config/empirical_ranges.json`** — SAR/AED/EUR bands derived from a frozen mid-2026 FX snapshot. Should be pulled from a rate API or maintained in a separate FX config the operator can refresh.
- [ ] **Raster-PDF drawings** — `drawing_qto`'s PDF path reads vector geometry via `page.get_drawings()`. Scanned/rasterised drawing PDFs need a CV-based dimension detector — new block, separate work.
- [ ] **sympy_reasoning `cost_impacts` array** — `_compute_qty_variances` populates the variance row's `cost_impact` field but the legacy `_compute_cost_impacts` aggregator still keys on the historical-benchmark symbol vars and produces nothing for the BOQ-vs-drawing path. The data is there in the variance entries; the aggregate list is empty. Low priority; downstream consumers can read per-row.
- [ ] **Validation -> LLM refusal pipeline** — runtime injects the `validation` field into the tool result the LLM sees, and the heavy-reasoning prompt says "refuse to report numbers whose validation.overall == fail". Not measured live whether the LLM actually obeys this on a real failed verdict. Needs a probe.
- [ ] **GDRIVE_SERVICE_ACCOUNT_JSON unset** — hydration step 2 silently no-ops. Needs an actual service account JSON to enable nightly Drive ingestion (separate from the per-user OAuth walker, which works).
- [ ] **Tests** — added auto-validation middleware, usage tracker, validation_pipeline block, exports router, Drive walker, sympy BOQ-vs-drawing path, and several others without unit tests. CI workflow won't catch a regression. Tech debt.
- [ ] **No CI gate on test coverage** — `diff-cover` job ships with `continue-on-error: true`; the audit agent flagged this earlier in the session.


## Operational housekeeping

- [ ] **Backup the 1 GB Render data disk** — projects, documents (encrypted), agent memory, usage. One bad write or accidental container rebuild loses everything. Nightly tarball to a cheap blob store would be enough.
- [ ] **Rotate / properly audit the secrets** — operator preference per memory is to NOT rotate the committed keys (`the-fork-env-committed`), but the DeepSeek key is at -$0.16 and Groq is the active provider. Worth a top-up if returning to DeepSeek.
- [ ] **Drive walker max_files = 100 hard cap** — fine for a smoke run, low for production drives that contain thousands of files. Either page it or expose pagination cursors.
- [ ] **Cost monitoring TPD warnings** — Groq free tier records tokens at $0 USD but daily TPD caps are real (100K on llama-3.3-70b, 500K on smaller models). The usage tracker stores raw tokens but doesn't warn before the cap; the React rail has no badge.


## Resumable agent runs from earlier tonight

Two parallel agents hit the Anthropic session limit at 02:40 Riyadh and never committed. Their branches are empty and were never merged. If you want them resumed:

- agentId `aa391c7696d24b8b5` (worktree `worktree-agent-aa391c7696d24b8b5`) — auto-validation middleware. Superseded; I built it inline later. Branch can be deleted.
- agentId `ae90492923f08e1f2` (worktree `worktree-agent-ae90492923f08e1f2`) — cost monitoring router. Also superseded; built inline. Branch can be deleted.

The other four worktree branches DID land (Tinker pipeline, ODA Dockerfile, validation tightening, drive walker pre-curser) — already merged + pushed.


## Commits that landed tonight (newest first)

- `2c0e804` fix(composer): use canonical getToken() for upload auth
- `364663d` fix(runtime): unwrap UniversalBlock envelope before collecting numerics
- `94983f5` refactor: remove tonight's hard-wiring; sympy BOQ-vs-drawing; no emojis
- `ce4b2c6` fix(usage): plumb user_id through chat/chat_stream/_call_llm
- `eaf1c53` feat: chat attachments + Google-native Drive imports + cost tracking
- `c4563a8` feat(drive): walk-and-index POST /v1/projects/{id}/drive/index-folder
- `669e2e1` feat(runtime): auto-validate every numeric tool result via validation_pipeline
- `2d3778b` fix(validation): dimensional check accepts construction shorthand + currency
- `0e50664` fix: close 4 gaps - semantic embeddings, runnable validation, PDF drawings, DWG conversion
- `0e53ca6` feat(validation): tighter slack, per-currency ranges, metric inference
- `d3a221c` feat(docker): bundle ODA File Converter
- `039787f` feat(training): wire Tinker SDK with default dry-run
- `73b8a7b` feat(exports): xlsx + docx + pdf schedule export endpoints
- `cc0ec63` fix(agents): generate_wbs target_count accepts integer OR string
- `a8c6410` fix(upload): allow .dxf .ifc .xer .rvt .tif extensions
- `eb3a1c6` fix(blocks): wire encrypted-file decryption into 4 construction parsers
- `ac1c707` fix(blocks): same fix in bim / bim_extractor / image
- `839ec22` fix: boq_processor column aliasing + slim heavy-reasoning prompt
- `90deb4e` fix: top_k schema, /v1/project/ask IDOR, Groq abstraction, bootstrap fail loud
- `62a2d8c` fix(agents): recover Llama-native tool-call markup from Groq tool_use_failed
- `d6ed7ac` feat(agents): Groq provider + plug live-test LLM credit leak


## Next session: pick from here

Ordered by what unlocks the most for the operator's stated goal (training the AI on real construction docs):

1. Wire the soft daily cap short-circuit in `runtime.py::_call_llm`. Tiny change, real safety.
2. Fire `scripts/run_tinker_training.py --execute` against the real docs already in project `fb776aa2`. Produces the first actual LoRA adapter.
3. Sit at the deploy in Edge and human-click the composer 📎 / 📷 / 🎤 buttons to confirm OS dialogs work as expected. (Yes the emojis are only in this list to refer to the visible UI; the code labels are plain text per the no-emoji rule.)
4. Build the artifacts side panel — even a minimal one that lists past WBS generations + download buttons would change the UX meaningfully.
5. Migrate `chat.py` block to `_llm_config()` so `LLM_PROVIDER=groq` covers every code path, not just the agent runtime.
