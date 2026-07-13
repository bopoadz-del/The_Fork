# CONSTRUCTION_LEDGER.md — the single honest statement of the construction brain

Read from current `main` (2026-07-13). Every row cites file:line. Master checklist for the
Total-Functionality Program. A row is **done** only when REAL(+reachable, live-proven) /
HONEST-STUB (named requirement) / PARKED-BY-DESIGN (verified dormant). No FAKE / BROKEN / UNREACHABLE
/ undocumented rows may remain at exit.

## COUNT (container census — Sections A+B; C/D/E appended as the lib/agents/docs census lands)
| Verdict | Count |
|---|---|
| REAL | 86 |
| REAL-BUT-UNREACHABLE | 4 |
| HONEST-STUB | 14 |
| FAKE | 0 |
| BROKEN | 0 |
| **Total** | **104** |

> **2026-07-13 — F1/F2/F3 CLOSED (PR #206 merged, deployed, live-accepted).** The 3 FAKE rows now
> return honest, requirement-naming errors instead of fabricated facts. Live acceptance via fork_cli
> through prod-config chat on `dar_al_arkan_master` (evidence: `review_pack/brain/accept_*_20260713.txt`):
> as_built (no files) → "requires as-built+design files or measurements+design_measurements", **no APPROVED**;
> tender_bid (1 bid) → "requires at least two real bids", **no award**; claim (no events, routed to
> forensic_delay_analysis) → "requires baseline + updated XER", **no invented quantum**. FAKE count 3→0.

## ACTION QUEUE (what the program must flip)
| # | item | verdict | fix | file:line | oracle |
|---|---|---|---|---|---|
| F1 | `as_built_deviation_report` (file path) | **DONE #206 ✓live** | real drawing_qto extraction+compare; None→honest error; no-files never APPROVED | `__init__.py:523-548`, `documents.py:583-633` | ✓ live: no files → "requires files/measurements", no APPROVED |
| F2 | `claims_builder` | **DONE #206 ✓live** | invented DE-001/DE-002 fallback removed → honest "needs delay_events" | `schedule.py:609-613` | ✓ live: no events → honest error, no $-quantum |
| F3 | `tender_bid_analysis` | **DONE #206 ✓live** | 3 invented bidders removed → honest "needs ≥2 bids" | `boq.py:1138-1143` | ✓ live: 1 bid → honest error, no award |
| F4 | `daily_site_report._fetch_weather` | **DONE (branch) ✓unit** | real Open-Meteo (geocode→archive/forecast, keyless) + honest `status:unavailable` on geocode-miss/bad-date/no-record/network; WMO code→conditions, real precip+wind→impact; narrative shows "unavailable(reason)" not fabricated | `__init__.py:736-838` | ✓ 9 unit tests: no-loc/bad-date/geocode-miss/no-record/network→unavailable; real payload→real reading (WMO0→clear, not "sunny"); archive path→adverse |
| W1 | orphaned: `track_progress`, `generate_construction_report`, `qa_inspection`, `progress_tracking` | REAL-BUT-UNREACHABLE | wire routing (or delete the 2 redundant) | `__init__.py:1410-1542` | reachable via chat phrasing |
| W2 | `digital_twin_sync` | REAL (name overpromises) | rename or wire a real push; add sync_status | `documents.py:1004-1054` | honest about no live push |
| W3 | `document_engine`/`process_document` llm_enhancer | dead sub-feature | fix `json` NameError (swallowed) or remove | `documents.py:167` | enhancement runs or is removed |
| V1 | `resource_histogram` (container action) | **DONE (branch) ✓live-DG2** | CONFLICT RESOLVED: the container action was BROKEN (dead-twin) — it called `_calculate_labor_histogram`, which read `early_start`/`resources.labor` keys the parser never produces → all-zero `status:success` with a hardcoded 30/20/15/15/20 trade split (fake success). The **lib** `pm_computations.resource_histogram` is REAL (hand-verified). FIX: deleted the broken helper cluster; rewired the action to parse the real `.xer` (`parse_xer_full`) → CPM → lib histogram over real TASKRSRC, **filtered to RT_Labor only** (a real DG2 baseline mixes an RT_Mat cost resource whose qty is ~1000× all labor — summing it fabricated 1.77bn "man-hours"); honest error when no `.xer`/activities/TASKRSRC/labor. | `schedule.py:546-679`, `pm_computations.py:417-501` | ✓LIVE on real DG2 baseline (11,149 acts / 47,615 TASKRSRC): labor-only **5.67M man-hrs**, 42 trades, peak M22 (evidence `review_pack/brain/accept_resource_histogram_dg2_20260713.txt`). ✓6 unit tests incl. RT_Mat exclusion. Live via predefined dispatch awaits Phase-2 schedule_file passthrough (honest error meanwhile). |
| V2 | `esg` social/governance | REAL-w/-honest-zeros; live shows composite "73.3" | verify rating excludes zeroed pillars OR labels clearly (Part B W1) | `boq.py:1756-1777` | partial inputs → honest partial rating |
| — | `construction_v2` | **REMOVED (PR #204 merged)** — census read pre-merge | — | — | — |

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
REAL-BUT-UNREACHABLE: track_progress, generate_construction_report, qa_inspection, progress_tracking. [W1]
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
- `scope_extractor` brief-only path **BROKEN** (document_engine only detects files by extension; text brief → error). fix: accept `{"text":...}` synthetic doc. `scope_extractor.py:76-80` / `document_engine.py:103-113`.
- `code.execute` runs **unsandboxed** despite "Sandboxed" docstring (security-label, not fabrication). gate through `_analyze`/`sandbox`. `code.py:171-215`.
- `pdf_v2`/`ocr_v2` dead twins → delete or wire in place of `pdf`/`ocr`.
- `historical_benchmark` REAL + self-disclosing (source_note/confidence) — name overclaims; rename candidate.

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
Overwhelmingly REAL. Fabrication surface: **3 container rows (F1/F2/F3 — FIXED, PR #206)** + weather stub (F4) + construction_v2 (deleted). Remaining program = wire BUILT-UNWIRED (EVM, project_reasoner, dormant agents — decide), fix broken (scope_extractor brief-path, resource_histogram verify), reconcile config-rot (historical_benchmark), Phase-2 param-extraction + grounding gate, correct 10 stale docs, then battery.

## SECTION E — docs/README designed-vs-built (census landed): 18 LIVE · 8 BUILT-UNWIRED · 5 PARKED-BY-DESIGN · 3 DOC-ONLY · 10 STALE
**BUILT-UNWIRED (code real, no live product caller — wire or ledger why):**
- EVM: `pm_excel.py` formula-linked EVM workbook (`/export/evm` no caller), `construction_knowledge.calculate_evm` (only offline caller), `progress_tracker` real SPI/EV (`/v1/projects/{id}/progress` no frontend caller). Chat EVM is **prompt-only** (`construction_evm.md` injected). → wire one path or document.
- `project_reasoner` UNDERSTAND→PLAN→EXECUTE→DELIVER: registered + `/v1/project/ask` endpoint but ZERO frontend callers (dormant for product). W6 target.
- Learned chat-routing (`routing_mode="learned"` never set), LoRA local model (`use_local_model` no caller), Drive→training pipeline (admin/API-only). → decide wire vs park.
- `STEP_EARNED_VALUE` plan-step declared but no executor calls it.
**PARKED-BY-DESIGN (verified dormant, must not fire):** EDGE_PORT/Orin (jetson_gateway.py doesn't exist), layered-RAG (single Master Corpus only), self-coding agent (router only redirects to heavy-reasoning, never self-coding), photo-BIM geo-anchoring, ResNet-18 CNN head (no code).
**STALE docs to correct:** README "RRF fused" (RRF discarded — retriever.py fusion seam, HIGH), photo_chunks removed (migration 0008), self-hosted-llm "no failover" (now built), reasoning-consolidation "paper only" (shipped, flag ON), ROADMAP_ML embedding stack (superseded by model2vec+pgvector).
**LIVE (confirmed):** doc-grounded Q&A+citations, BOQ extraction, QTO(PDF), CPM-WBS, drawing-vs-BOQ variance, cited KB, RAG injection gate, Ollama provider, predefined schedule workflow, Scoped-Dispatch allowlist (reachable — but subset fabricates, = the F1-F3 fixes + remaining).
