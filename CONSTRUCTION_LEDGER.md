# CONSTRUCTION_LEDGER.md — the single honest statement of the construction brain

Read from current `main` (2026-07-13). Every row cites file:line. Master checklist for the
Total-Functionality Program. A row is **done** only when REAL(+reachable, live-proven) /
HONEST-STUB (named requirement) / PARKED-BY-DESIGN (verified dormant). No FAKE / BROKEN / UNREACHABLE
/ undocumented rows may remain at exit.

## COUNT (container census — Sections A+B; C/D/E appended as the lib/agents/docs census lands)
| Verdict | Count |
|---|---|
| REAL | 86 |
| REAL-BUT-UNREACHABLE | 0 (was 4 — W1: 2 deleted, 2 → PARKED-BY-DESIGN) |
| PARKED-BY-DESIGN | 2 (track_progress, generate_construction_report — need multi-file resolution) |
| HONEST-STUB | 14 |
| FAKE | 0 |
| BROKEN | 0 |
| **Total** | **102** (104 − 2 deleted) |

> **2026-07-13 — F1/F2/F3 CLOSED (PR #206 merged, deployed, live-accepted).** The 3 FAKE rows now
> return honest, requirement-naming errors instead of fabricated facts. Live acceptance via fork_cli
> through prod-config chat on `dar_al_arkan_master` (evidence: `review_pack/brain/accept_*_20260713.txt`):
> as_built (no files) → "requires as-built+design files or measurements+design_measurements", **no APPROVED**;
> tender_bid (1 bid) → "requires at least two real bids", **no award**; claim (no events, routed to
> forensic_delay_analysis) → "requires baseline + updated XER", **no invented quantum**. FAKE count 3→0.
>
> **2026-07-13 — F4 CLOSED (PR #207 merged, live on 0a27dad, live-accepted).** `daily_site_report`
> weather now real Open-Meteo or honest "unavailable(reason)", never fabricated. Live on `dar_al_arkan_master`:
> report renders **"Weather: Not requested"** — the old hardcoded 35°C/sunny/favorable is gone (evidence:
> `review_pack/brain/accept_dailyreport_answer_20260713.txt`; a full-corpus scan of `review_pack/brain/`
> finds zero fabrication markers). The real-weather leg (geocode Riyadh → live reading) awaits Phase-2
> location passthrough — the NL "Riyadh" doesn't reach `_fetch_weather` through predefined dispatch, so the
> action takes the honest empty-weather path rather than fabricating. **FAKE count 4→0 — Phase-1 fabrication kill COMPLETE.**

## ACTION QUEUE (what the program must flip)
| # | item | verdict | fix | file:line | oracle |
|---|---|---|---|---|---|
| F1 | `as_built_deviation_report` (file path) | **DONE #206 ✓live** | real drawing_qto extraction+compare; None→honest error; no-files never APPROVED | `__init__.py:523-548`, `documents.py:583-633` | ✓ live: no files → "requires files/measurements", no APPROVED |
| F2 | `claims_builder` | **DONE #206 ✓live** | invented DE-001/DE-002 fallback removed → honest "needs delay_events" | `schedule.py:609-613` | ✓ live: no events → honest error, no $-quantum |
| F3 | `tender_bid_analysis` | **DONE #206 ✓live** | 3 invented bidders removed → honest "needs ≥2 bids" | `boq.py:1138-1143` | ✓ live: 1 bid → honest error, no award |
| F4 | `daily_site_report._fetch_weather` | **DONE #207 ✓live** | real Open-Meteo (geocode→archive/forecast, keyless) + honest `status:unavailable` on geocode-miss/bad-date/no-record/network; WMO code→conditions, real precip+wind→impact; narrative shows "unavailable(reason)" not fabricated | `__init__.py:736-838` | ✓ 9 unit tests: no-loc/bad-date/geocode-miss/no-record/network→unavailable; real payload→real reading (WMO0→clear, not "sunny"); archive path→adverse. ✓LIVE-accepted 2026-07-13 on dar_al_arkan_master: report renders "Weather: Not requested" — fabricated 35°C/sunny/favorable GONE (evidence `review_pack/brain/accept_dailyreport_answer_20260713.txt`). **Real-weather leg CLOSED (#216, Phase-2 2c): ✓LIVE-through-brain 2026-07-13** — project `location` field (Alembic 0010) + PATCH endpoint; predefined resolver fills `daily_site_report`'s location from project metadata (never scraped from the message). Live on prod project `31df5b9d` (location=Riyadh via PATCH): NL "generate today's daily site report" → REAL Open-Meteo weather (Riyadh, 42.9°C/30.6°C, mainly clear, wind 8.1 km/h, favorable) in the report (evidence `review_pack/brain/accept_f4_LIVE_20260713.txt`); no project location → honest empty, never scraped. Codex review caught (and I fixed) a real bug: daily_site_report read location only from params; now reads input_data too. |
| W1 | orphaned methods | **DONE (branch) ✓** | 2 redundant/dead DELETED: `qa_inspection` (thin wrapper → reachable `qa_qc_inspection`) + `progress_tracking` (honest-error redirect → reachable `progress_tracker`); both had 0 refs, never dispatched. 2 real+unique PARKED-BY-DESIGN: `track_progress` (real photo-vs-BIM via image block + IFC query, honest 0 when inputs absent) + `generate_construction_report` (real reshape of reachable `process_document`, referenced as a suggested_action) — both need multi-file/photo param resolution (the same 2-file class deferred in Phase-2 2b: which photo/which BIM), so PARKED not wired. | `__init__.py` (qa_inspection/progress_tracking deleted) | ✓ tests: redundant orphans gone, real twins remain |
| W2 | `digital_twin_sync` | **DONE (branch) ✓** | REAL but the name overpromised: it PREPARES the platform sync payloads (transform + operations + api_payloads + connection/auth info) but never PUSHES to a live twin. Added `sync_status: "prepared_not_pushed"` + a `note` making status:success mean "prepared", not "synced" (the ledger's "add sync_status" option; a full rename would touch handlers×2 + orchestrator patterns — deferred, the response is now honest). | `documents.py:1042-1075` | ✓ test: sync_status=prepared_not_pushed, note says no live push |
| W3 | `document_engine`/`process_document` llm_enhancer | **DONE #211 ✓** | dead since written (`json.dumps` w/o `json` imported → NameError swallowed by bare `except: pass`; `llm_enhanced` never set, no consumers). REMOVED the dead block — reviving it would fire an LLM call per document parse (cost/latency/provider-routing change, out of scope). Document processors already return real structured data. | `documents.py:160-168` | ✓ no `llm_enhanced` consumers anywhere; module imports clean |
| V1 | `resource_histogram` (container action) | **REAL ✓LIVE-through-brain (#209+#212)** | CONFLICT RESOLVED: the container action was BROKEN (dead-twin) — it called `_calculate_labor_histogram`, which read `early_start`/`resources.labor` keys the parser never produces → all-zero `status:success` with a hardcoded 30/20/15/15/20 trade split (fake success). The **lib** `pm_computations.resource_histogram` is REAL (hand-verified). FIX: deleted the broken helper cluster; rewired the action to parse the real `.xer` (`parse_xer_full`) → CPM → lib histogram over real TASKRSRC, **filtered to RT_Labor only** (a real DG2 baseline mixes an RT_Mat cost resource whose qty is ~1000× all labor — summing it fabricated 1.77bn "man-hours"); honest error when no `.xer`/activities/TASKRSRC/labor. Phase-2 (#212) resolves schedule_file from the project's uploaded .xer. | `schedule.py:546-679`, `pm_computations.py:417-501`, `chat.py:_resolve_predefined_file_params` | ✓**LIVE-through-brain on prod 2026-07-13** (project `31df5b9d`, real .xer uploaded via normal flow, encrypted at rest): NL "build the manpower resource histogram" → `mode:predefined` → resolver → **decrypt-on-read** → 700 man-hrs RT_Labor-only, LAB 600/CARP 100 (`review_pack/brain/accept_phase2_LIVE_20260713.txt`); 2 .xer → honest ask-which (`..._askwhich_...`); no .xer → honest error (`..._noxer_...`). ✓local 5.67M man-hrs on full DG2. ✓6 unit + 8 wiring tests. NOTE: full 25MB DG2 .xer 502s on upload — see upload-capacity finding below. |
| V2 | `esg` social/governance | **DONE #211 ✓unit** | was fabricating a composite + letter grade from placeholder zeros (`_score_social`→80 for `ltifr==0`=no-data; `_score_governance`→flat 70). Now each pillar is scored ONLY when its inputs are supplied (`boq` / `manpower`+`safety_records` / `project_data`); `overall` averages assessed pillars only; letter grade `A–D` only when all 3 present, else `null` (no data) or `"partial (n/3 pillars)"`; `data_status` discloses assessed vs missing. | `boq.py:1685-1722` | ✓3 unit tests: no-data→overall/rating `null`; env-only→`partial (1/3)`; all pillars→full letter grade (73.3→B) |
| — | `construction_v2` | **REMOVED (PR #204 merged)** — census read pre-merge | — | — | — |
| U1 | document upload path (large files) | **PARKED-with-evidence (transfer-bound, not fabrication, not encrypt)** | DIAGNOSED 2026-07-14: the 502 is NOT the buffer+encrypt (Fernet encrypt+write of 25MB = **0.27s** measured; decrypt 0.40s). It is HTTP **transfer time** exceeding Render's gateway timeout. Prod upload-scaling test (all HTTP 201, NO cliff): 1MB→3.2s, 2MB→5.4s, 5MB→15.4s, 10MB→22.8s (~2.3s/MB, linear ⇒ bandwidth-bound). The original 25MB 502 was a slow-upstream artifact (~9s/MB that night → 224s > gateway timeout). **Real fix = chunked/resumable upload (large feature: init/chunk/finalize endpoints + partial-upload state + client changes) — PARKED per "PARK-with-evidence beats rushed green".** Memory angle bounded by the 50MB `MAX_DOC_UPLOAD_SIZE` cap. Typical office upstream (10+ Mbps) uploads 25MB in ~20s → a non-issue for normal pilot clients/files; only very large files on slow links 502. Async-offloading the 0.27s encrypt would NOT help. | `projects.py:656-714`, `file_crypto.write_document` | a ≥25MB file uploads without 502 on a slow (<1Mbps) link (needs chunked upload) |

## SECTION A — ConstructionContainer actions
REAL (no fix): chat, process_document, qa_qc_inspection, extract_quantities, estimate_costs,
progress_tracker, bim_analysis, parse_primavera_schedule, process_contract,
process_specification_full, change_order_impact, rfi_generator, safety_compliance_audit,
carbon_footprint_calculator, procurement_list_generator, warranty_maintenance_schedule,
risk_register_auto_populate, submittal_log_generator, payment_certificate, bim_clash_detection,
value_engineering, commissioning_checklist, variation_order_manager, forensic_delay_analysis,
cash_flow_forecast, procurement_optimizer, procurement_analysis, esg_sustainability_report,
om_manual_generator, intelligent_workflow, auto_pipeline, health_check, generate_wbs, boq_process,
spec_analyze, sympy_reason, drawing_qto, primavera_parse, orchestrate, formula_execute, bim_extract,
learn, benchmark_lookup, recommend + 8 route()-only aliases (cost_estimate, analyze_spec,
schedule_risk, contract_review, safety_audit, carbon_report, procurement, status) + handover_management.
FAKE: none — as_built_deviation_report/claims_builder/tender_bid_analysis fixed to honest-gate [F1–F3 DONE #206, live-accepted 2026-07-13].
REAL-BUT-UNREACHABLE → RESOLVED [W1]: qa_inspection + progress_tracking DELETED (redundant/dead); track_progress + generate_construction_report PARKED-BY-DESIGN (real, but need multi-file/photo param resolution — the 2b-deferred 2-file class).
HONEST-STUB: jetson_dispatch (not-implemented), + 10 procedure actions (PRC-501/502/301/303/401/402/405/601/602/604) that return cited DB procedure metadata when no qualifying input, delegate to a real handler when input present. [acceptable — honest]

## SECTION B — Blocks (domain kit + platform) — ALL REAL
boq_processor, bim, bim_extractor, drawing_qto, primavera_parser, spec_analyzer, formula_executor_v2,
sympy_reasoning, smart_orchestrator, project_dashboard, learning_engine, recommendation_template,
historical_benchmark (transparently-limited static rate book), scope_extractor, schedule_generator,
cpm_engine, manpower_planner, fasttrack_analyzer, schedule_excel_writer, construction_advisor,
project_reasoner (possible parallel/legacy vs runtime orchestrator — reconcile W6), validation_pipeline,
voice, ocr, image, pdf, document_engine, google_drive, onedrive. All do genuine computation with
honest auth/empty-input gating. (construction_v2 row from census = now REMOVED.)

## SECTION B — blocks (census landed): 47 REAL · 2 dead-twin (pdf_v2, ocr_v2 REAL-BUT-UNREACHABLE) · android_drive HONEST-STUB · construction_v2 FAKE→**DELETED #204**.
New block-level actionables:
- `scope_extractor` brief-only path **FIXED (branch) ✓unit** — document_engine treated `{"text": brief}` as a file path (`splitext` on prose → no ext → "No input files"). Now: an inline brief (or `brief` key) that isn't a recognised document path is wrapped in a synthetic text document and reasoned over by the same reasoner/mapper pipeline files use. Verified: a 3-sentence brief → 4 real obligations extracted verbatim (`documents_parsed=1`), empty input still honest-errors. `document_engine.py:102-126,128-210` + `tests/test_scope_extractor.py`.
- `code.execute` runs **unsandboxed** despite "Sandboxed" docstring (security-label, not fabrication). gate through `_analyze`/`sandbox`. `code.py:171-215`.
- `pdf_v2`/`ocr_v2` dead twins → delete or wire in place of `pdf`/`ocr`.
- `historical_benchmark` **RESOLVED (branch) — KEPT + IMPROVED** (operator decision 2026-07-14: keep as the day-one fallback, expand to multi-country). Now: 108 location keys across all regions (was ~22 GCC-centric); each of the 14 base rates declares its cost **basis** (material-only / all_in / plant_labour) in the response; unknown location → `location_matched:False` (priced at US base, disclosed not silent); `list_locations` action. `generate_cost_estimate` surfaces per-line basis + a `mixed_basis` warning (material-only lines exclude placing/fixing labour → total understates labour-heavy trades) + `rate_source:indicative_benchmark_fallback`. **Config-rot fixed in the same pass:** the "was removed" claim corrected in 3 agent configs (learning/quantity-surveyor/validation) + 2 boq.py messages — the block is a labelled indicative fallback, prefer client/supplier rates. Long-term: client-loaded rates + wired learning loop (W6) supersede it.
  **DEMOTED 2026-07-14 (PR #222) — rates moved to RAG.** Operator directive: the LLM
  must never answer cost from parametric knowledge. New two-layer model: (1) COMPANY —
  the client's priced-BOQ unit rates, now retrievable as clean project-RAG chunks after
  fixing the `.xlsx` extractor to be row-wise (was a space-joined cell blob that
  separated a rate from its item); (2) GK FALLBACK — real sourced Gulf/KSA benchmarks
  (`docs/knowledge/construction_rates_gulf_ksa_2025.md`, Turner & Townsend/AECOM/Saudi
  Contractors Authority) auto-seeded into the GK project. Company rates outrank GK via the
  existing `RAG_GK_SCORE_MARGIN`. Agent configs (project-assistant/heavy-reasoning) tightened:
  quote a rate ONLY from retrieved context (company→GK, cite source+year) else REFUSE. The
  `historical_benchmark` dict is now a last-resort labelled fallback only; learned rates over
  time route through the existing `ProcurementLeadTimeLearner`/`DurationCalibration`, not json.
  **COST-FABRICATION FIX (2026-07-14, branch fix/cost-query-fabrication).** Live test found the
  rates-in-RAG work was necessary but not sufficient: a query "concrete (250 kg/cm2)" made
  `extract_query_identifiers` emit the unit `kg/cm2`, whose +2.0 identifier bonus pulled DG2
  drawing dimension-tables above the real rate chunk, and the LLM fabricated "450 SAR/m³" from
  the number-soup. Two fixes in one PR: **(a)** `_looks_like_unit()` excludes measurement units
  from identifiers (doc-codes IP-INF-054/PRC-501/D999.46 untouched); **(b)** `_cost_grounding_gate`
  in `agents/runtime.py` — a cost/rate figure must trace to a RATE-SEMANTIC retrieved chunk
  (currency/price context) or a computed tool result, else the answer is refused (flag
  `COST_GROUNDING_GATE`, default on). The gate grounds figure+semantics, NOT the bare number, so
  the incident refuses even though "450" is in retrieval. Golden case `cost_fabrication_concrete_rate`
  added. **DEFERRED CLASS FIX (own row):** drawing number-table chunks are low-semantic content
  that pollutes ANY numeric query — tag them at ingestion and exclude from cost/rate answering.
  This PR fixes the instance (unit-identifier + gate); the class fix (chunk classification) is
  separate.

## SECTION C — app/lib modules (census landed): 8 REAL, 0 FAKE/BROKEN — the best-tested code in the repo
- `pm_computations` (CPM: topo/forward/backward/float/resource_histogram/xer parse/compress) — REAL, ~40 assertions, hand-verified networks. No gaps in the math.
- `boq_excel.generate_cost_boq` — REAL, **numerically proven** (workbook re-loaded, formulas evaluated to correct total 2,181,000).
- `boq_pricing` — REAL, real dated/sourced rate-card (`boq_rate_card.json`), never invents a rate.
- `boq_units` — REAL (CESMM4/POMI inference, 22 cases).
- `pm_excel` (cost-loaded schedule + EVM workbook) — REAL; EVM **CPI value-proven**, other 6 metrics shape-only; local `_cpm` duplicates pm_computations (dedupe candidate).
- `schedule_bridge` — REAL, explicit anti-fabrication honesty contract, tested.
- `excel_templates` — REAL formatting helpers.
- `schedule_feed` — REAL logic (drops ontology_default lead-times) but **no direct test** (both call sites monkeypatch it out). fix: add `tests/test_schedule_feed.py`.

## SECTION D — 14 agent configs (census landed): 3 reachable, 10 DORMANT, 1 PARKED
- REACHABLE-REAL: **project-assistant** (UI default), **heavy-reasoning** (sole redirect target). REACHABLE via delegation only: **smart-orchestrator** (named in project-assistant prompt).
- DORMANT (raw `POST /v1/agents/{name}/chat` only, never surfaced by UI or delegation): bim-analyst, document-ingestion, external-mcp, learning, document-analyst, construction-pm, contracts-manager, quantity-surveyor, safety-officer, validation. **W7 decision: wire (add can_delegate + name them in a reachable prompt) or document as design-only — one honest row each.**
- PARKED-BY-DESIGN: **self-coding** (no reachable delegator, self-limited prompt, verified must-not-fire).
**Config-rot actionables (1i + W7):**
- `historical_benchmark`: 4 configs (learning/construction-pm/quantity-surveyor/validation) say "removed" but it EXISTS + is REQUIRED by project-assistant (contract-tested). Reconcile: update the 4 prompts OR retire consistently. [1i]
- 7 configs have unexecutable "hand off to X agent" prose (no `can_delegate` → delegate tool never added).
- `document-ingestion`/`external-mcp` allow blocks (`local_drive`/`google_drive`/`onedrive`/`mcp_consumer`) gated behind `CEREBRUM_VIRGIN=false` — silently skipped under virgin boot. Verify live `CEREBRUM_VIRGIN`.
- Missing test: parametrized `allowed_blocks ⊆ BLOCK_REGISTRY` per agent + "no orphan block" lint. Add it.

---
## LEDGER STATUS
Container(A+B) ✓ · Blocks(B) ✓ · Lib(C) ✓ · Agents(D) ✓ · Docs(E) ✓ — **full designed surface covered.**
Overwhelmingly REAL. Fabrication surface: **ALL 4 killed — F1/F2/F3 (PR #206, live-accepted) + F4 weather (PR #207, live-accepted 2026-07-13)** + construction_v2 (deleted). FAKE count 4→0. Remaining program = wire BUILT-UNWIRED (EVM, project_reasoner, dormant agents — decide), fix broken (scope_extractor brief-path, resource_histogram verify), reconcile config-rot (historical_benchmark), Phase-2 param-extraction + grounding gate, correct 10 stale docs, then battery.

## SECTION E — docs/README designed-vs-built (census landed): 18 LIVE · 8 BUILT-UNWIRED · 5 PARKED-BY-DESIGN · 3 DOC-ONLY · 10 STALE
**BUILT-UNWIRED (code real, no live product caller — wire or ledger why):**
- EVM: `pm_excel.py` formula-linked EVM workbook (`/export/evm` no caller), `construction_knowledge.calculate_evm` (only offline caller), `progress_tracker` real SPI/EV (`/v1/projects/{id}/progress` no frontend caller). Chat EVM is **prompt-only** (`construction_evm.md` injected). → wire one path or document.
- `project_reasoner` UNDERSTAND→PLAN→EXECUTE→DELIVER: registered + `/v1/project/ask` endpoint but ZERO frontend callers (dormant for product). W6 target.
- Learned chat-routing (`routing_mode="learned"` never set), LoRA local model (`use_local_model` no caller), Drive→training pipeline (admin/API-only). → decide wire vs park.
- `STEP_EARNED_VALUE` plan-step declared but no executor calls it.
**PARKED-BY-DESIGN (verified dormant, must not fire):** EDGE_PORT/Orin (jetson_gateway.py doesn't exist), layered-RAG (single Master Corpus only), self-coding agent (router only redirects to heavy-reasoning, never self-coding), photo-BIM geo-anchoring, ResNet-18 CNN head (no code).
**STALE docs to correct:** README "RRF fused" (RRF discarded — retriever.py fusion seam, HIGH), photo_chunks removed (migration 0008), self-hosted-llm "no failover" (now built), reasoning-consolidation "paper only" (shipped, flag ON), ROADMAP_ML embedding stack (superseded by model2vec+pgvector).
**LIVE (confirmed):** doc-grounded Q&A+citations, BOQ extraction, QTO(PDF), CPM-WBS, drawing-vs-BOQ variance, cited KB, RAG injection gate, Ollama provider, predefined schedule workflow, Scoped-Dispatch allowlist (reachable — but subset fabricates, = the F1-F3 fixes + remaining).

## PART B / STEP 4 STATUS (2026-07-14)
**Phase-2 grounding gate: COMPLETE** — inc.1 stamps (#215) + inc.2 money/rate (#219), FLAG-only, flag-gated `GROUNDING_GATE`, zero-FP verified (147 real financial figures). `predefined_reasoning._apply_grounding_gate`.

| W | area | status | evidence |
|---|---|---|---|
| W1 | financial | **DONE** | money/rate grounding gate (#219) + financial arithmetic hand-tested (payment_certificate net_due=120000 etc.) |
| W2 | schedule engine | **DONE** | hand-solved CPM oracle committed (#219, textbook net → dur 14, path A-B-D-F, all ES/EF/LS/LF/float); .xer round-trip + resource_histogram live-verified (#212); pm_excel EVM value-proven (§C) |
| W3 | drawings/BIM/vision | **PARKED-w-evidence** | needs DXF regression suite + planted-clash IFC fixture + photo-path smoke. Blocks REAL (ezdxf/ifcopenshell); gap = committed oracle fixtures. Multi-day. |
| W4 | document intelligence | **PARKED-w-evidence** | needs contract/spec extraction oracles + Arabic e2e. Multi-day. |
| W5 | orchestration | **PARKED-w-evidence** | needs full routing matrix (intent→action table+test) + dual-orchestrator reconciliation (keyword vs dynamic understand_intent). Partially mapped. |
| W6 | reasoning-engine live activation | **PARKED-w-evidence** | the #166/#167 finding — construction_learning recorders never called; no ActivityGraph built from a live project. Wiring a real data feed = the activation. Substantial. |
| W7 | 14-agent liveness | **PARKED-w-evidence** | §D audit: 3 reachable, ~10 dormant, 1 parked. Needs decision to wire vs ledger-PARK each dormant agent. |
| W8 | KB & prompts coverage | **PARKED-w-evidence** | knowledge-md→GK seeding live (FIDIC); needs coverage-vs-gaps audit. |
| W9 | dashboard/exports | **PARKED-w-evidence** | export libs value-proven (§C); needs endpoint round-trip smoke (files OPEN correctly). |
| W10 | designed-vs-built | **PARKED-w-evidence** | largely captured in §E (BUILT-UNWIRED/PARKED/STALE); needs final sweep. |

**STEP 5 (RAG SOP ingestion): NOT STARTED** — 5a SOP folders (no approval; needs Drive access this session), 5b ⛔ SOURCE_MANIFEST gate (Chadi), 5c referee.
**STEP 6 (battery + PILOT_READINESS rebuild): TERMINAL, not yet run.**
See `HANDOFF.md` for the full continuation plan.
