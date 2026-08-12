# PILOT READINESS — The_Fork / The Shovel — 2026-07-12

Autonomous completion run against **live prod** (`the-fork-jn3t.onrender.com` / `theshovel.ai`, srv-d8hdc6ek1jcs739rq5sg). Honesty rule in force: DONE = acceptance passed with evidence; otherwise PARKED with evidence. **Pilot go/no-go is Chadi's call (G4)** — this page is the input to it.

## Verdict in one line
The **foundation is now sound and the construction platform is actually loaded** — but a full feature/eval battery has not been run end-to-end, so this is **"substantially ready, verification-incomplete,"** not a green light.

## What WORKS (evidence)
| Area | Evidence |
|---|---|
| **Blocks / construction platform** | Prod `/v1/health` = **40 blocks, 0 failures** (was 17 — construction kit was unset). Construction container, boq_processor, drawing_qto, bim_extractor, primavera_parser, spec_analyzer, cpm_engine, schedule_generator, smart_orchestrator, project_reasoner all loaded. |
| **Chat backend** | `AGENT_TIMING_LOG` shows real completions: `chat_stream iter=0 1.5s tools=['search_project_documents']`, `iter=1 2.3s tools=final`, `STREAMING-FINAL chars=294 cum=4.0s`. **2–4s, RAG tool calls, streamed answers.** Not hanging. |
| **Grounded RAG answers** | Live UI answers cite real docs+chunks (e.g. payments → DD-2022-175 Vol 4 Schedules; commissioning → Aecom electrical comments, drawing refs, DD-2023-118 Vol 2). |
| **Corpus** | `chunks_v2` = **53 docs / 10,502 chunks**, avg **198 chunks/doc**, **0 duplicate chunk_ids**, canonical store confirmed. Large contracts fully chunked (up to 1,936). |
| **DB integrity** | `DATABASE_URL` set + **fail-loud guard shipped** (#190) so the "empty SQLite" strand can't recur. Old `chunks` table empty; `chunks_v2` canonical. |
| **Master corpus** | `MASTER_CORPUS_SOURCE_PROJECT_ID=client_infra_pack_1` → "Master Corpus" now retrieves the real corpus (was empty). |
| **Auth** | 401s enforced; both domains serving; logged-in session working. |

## Shipped this run
- #189 — warm-load RAG embedder at boot (kills cold-load hang class).
- #190 — require DATABASE_URL in prod + RECONCILIATION.md.
- Env: `CEREBRUM_DOMAIN_KITS=construction` (T1), `MASTER_CORPUS_SOURCE_PROJECT_ID=client_infra_pack_1`, `AGENT_TIMING_LOG=1`.

## PARKED — with evidence (what's NOT verified)
| # | Item | Evidence / why parked | To unpark |
|---|---|---|---|
| T2 | **Sentry disabled** | `SENTRY_DSN` unset (Chadi's secret — cannot fetch). AGENT_TIMING_LOG set as interim. | **G-gated: Chadi provides SENTRY_DSN.** |
| T3 | **20-turn cold-start verification** | Backend proven healthy via timing logs (2–4s). Browser automation unreliable for SSE (sends intermittently don't fire / don't render — automation artifact, not backend). | Run `fork_cli --events` (backend-direct) for 20 turns incl. 3 cold starts. |
| T3c | **SYNTHESIS_STREAMING** | Exists, OFF, unvalidated for openai path. | Validate ON (branch→smoke 10→deploy). |
| T4 | **Corpus coverage** | Only 53 docs in v2 (the client project pkg + GK + a little Ha Long Xanh). Full Drive archive NOT in v2 (no `drive_archive` table). Non-the client project project shells near-empty. | Decide pilot scope (the client project-only?) or re-ingest tier-2/3. |
| T4b | **RFP under-extraction** | Anthropic/Kenya RFPs = **2 chunks each** (docx/xlsx extractor grabbed little) → weak RFP reasoning. `.kmz` over-chunked to 810 (noise). | Re-extract RFP class; exclude/limit `.kmz`. |
| T5 | **Retrieval precision on v2** | Live-path recall eval: all 15 Qs **HIT (retrieved=5)** — recall is fine. But the **top chunk for every the client project question is one FIDIC GK note (`980e19f6`)** — GK notes crowd out project-specific clauses → LLM says "could not find [the client project-specific]". **VALIDATED the auditor's warning:** enabling cfg7 knobs (MARGIN=0.15/BOOST=0.1/CAP=2) makes `retrieve_with_filter` return **EMPTY on v2** — the old-index tuning does NOT transfer. **Decision: keep knobs OFF on prod** (retrieval works, GK-dominated) — do NOT ship cfg7 (breaks v2). | **Re-sweep knobs on v2** (grid + eval referee, per T5); GK demotion must be re-derived, not copied. |
| T6 | **Feature matrix / golden set / 100-q recall** | Construction kit now loaded (features AVAILABLE), chat backend healthy. Full 68-run matrix + 28 golden + 100-q not executed (LLM compute + auth). | Run `scripts/feature_matrix_sweep.py`, golden set, recall eval → FEATURE_MATRIX_FINAL / GOLDEN_SET_REPORT / RAG_AUDIT_V4. |
| T6b | **Attachment reasoning** | Files DO upload+index (uploaded RFPs present in client). But `[attached: X]` marker isn't parsed server-side and RFPs under-extracted (2 chunks) → "work on the attached file" weak. | Wire `[attached:X]`→fetch that doc/route to document_engine; fix RFP extraction. |

## Open human decisions (for Chadi)
1. **SENTRY_DSN** (T2) — provide it to restore observability.
2. **Pilot corpus scope** (T4) — the client project-package-only, or ingest the full Drive archive into v2?
3. **Pilot go/no-go** (G4) — this page + a completed T5/T6 battery are the inputs.

## Bottom line
The platform went from "reading an empty database with the construction kit switched off" to "reading the real corpus with 40 blocks loaded, 2–4s grounded answers, and recurrence guards in place." The remaining gap is **verification breadth** (feature matrix, precision battery, golden set) and two known quality fixes (retrieval precision on the mixed corpus, RFP extraction). Those are the pilot conversation.
