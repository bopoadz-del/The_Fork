# Known Incomplete

The honest register. `scripts/audit_stubs.py` walks the tree, finds every
function with a hollow body, and exits non-zero on any that is not listed
here. So nothing in this repository can be quietly hollow: it is either
implemented, or it is on this page with a reason.

Read the three sections differently. Only the first is a gap.

- **Roadmap** — real features that are genuinely not built. A caller reaches
  them and gets nothing back. None of them fabricates a value.
- **Correct by design** — code the audit's heuristic cannot distinguish from a
  stub. An empty body is the right answer here, and changing it would be wrong.
- **Test harness** — fakes inside dev scripts. Never shipped, never reached by
  a user.

Last reconciled against `scripts/audit_stubs.py`: 2026-08-03.

---

## Roadmap — deferred, and a caller gets nothing

All of these live in `app/containers/construction/`. They are the deep
domain-intelligence layer: the container's shipped paths call them, get an
empty list back, and continue.

**They return empty, never fabricated.** That distinction is the whole point.
An O&M manual with an empty maintenance section is a manual a client can see
is incomplete. An O&M manual with invented maintenance intervals for their
chilled-water plant is a liability. Where one of these feeds a client-facing
deliverable, the output now names the gap explicitly — `om_manual_generator`
returns `status: "partial"` and a `data_gaps` list saying which sections could
not be populated and what data they need.

What they all have in common: each needs a data source the platform does not
have yet — manufacturer maintenance schedules, supplier part numbers, a
trained vision model for site photos, or a bid-comparison corpus. They are not
blocked on code.

### O&M manual — needs manufacturer maintenance data

- app/containers/construction/__init__.py :: _generate_daily_tasks  — Section E daily PM schedule; needs per-equipment manufacturer intervals.
- app/containers/construction/__init__.py :: _generate_weekly_tasks  — Section E weekly PM schedule; same source.
- app/containers/construction/__init__.py :: _generate_quarterly_tasks  — Section E quarterly PM schedule; same source.
- app/containers/construction/__init__.py :: _create_maintenance_matrix  — Section E responsibility matrix (task x equipment x trade).
- app/containers/construction/__init__.py :: _generate_troubleshooting_guide  — Section F; needs manufacturer fault codes and remedies.
- app/containers/construction/__init__.py :: _generate_spare_parts_list  — Section H; needs supplier part numbers and consumable intervals.
- app/containers/construction/__init__.py :: _extract_training_needs  — operator competency requirements per installed system.
- app/containers/construction/__init__.py :: _map_system_dependencies  — Section B interdependencies; needs a system topology model, not an equipment list.

### Site photo intelligence — needs the vision model wired through

The Safety Observation model exists (YOLO-Worldv2 ONNX); these three are the
unbuilt bridge from its detections to structured daily-report fields.

- app/containers/construction/__init__.py :: _extract_equipment_from_photos  — plant/equipment on site from photo analysis.
- app/containers/construction/__init__.py :: _extract_quality_observations  — workmanship observations from photo analysis.
- app/containers/construction/__init__.py :: _extract_material_deliveries  — deliveries from voice transcriptions.

### Tender and procurement analysis — needs a bid corpus

- app/containers/construction/__init__.py :: _identify_qualification_gaps  — what a bid fails to qualify against the tender requirements.
- app/containers/construction/__init__.py :: _identify_bid_clarifications  — clarifications to raise with a bidder.
- app/containers/construction/__init__.py :: _identify_consolidation  — procurement packages worth consolidating.
- app/containers/construction/__init__.py :: _suggest_bundling  — supplier bundling opportunities.
- app/containers/construction/__init__.py :: _identify_procurement_risks  — single-source and long-lead risk on a procurement plan.

---

## Stated limitations — implemented, but narrower than it looks

Not hollow functions, so `audit_stubs.py` cannot see these. They are here
because the honest scope of a feature is part of knowing what is incomplete.

**A drawing is only recognised by its FILENAME.** `_drawing_chunks_for_document`
gates on `_looks_like_drawing`, which matches `dwg`/`dwgs`/`drawing`/`drawings`
in the filename of a PDF. A drawing named something else — `Sheet 4 of 12.pdf`,
`site plan final.pdf` — is missed, and its schedules reach retrieval only as
loose text.

Deliberate, and it is the same trade `_looks_like_boq` makes for the same
reason: running table detection over every page of every PDF would add real
cost to a bulk re-index, and re-encode runs on this corpus already sit close
to a capacity wall. Bare `sheet` was considered as a token and rejected — it
matches "Data Sheet" and "Method Statement Sheet", which buys breadth in
exactly the wrong direction.

The regex is pinned in `tests/e2e/test_f7_drawing_schedule_retrieval.py`
against real filenames taken from the indexed corpus, both the drawings it
must match and the BOQ/spec/plan/contract documents it must not. Widening it
means adding the new filename shape to that list first.

**Title-block values on the line below their label are missed.** Every
separator in `_extract_title_block` is `[ \t]*` rather than `\s*`, so a value
is only read from the same line as its label. Some CAD title blocks put the
value on the next line and those fields come back `None`. Deliberate: letting
the gap cross a newline makes an empty `DRAWN BY:` capture the following
`CHECKED BY` label as the drafter's name. Missing a field is recoverable;
inventing one on a client drawing is not.

---

## Correct by design — an empty body is the right answer

The audit flags these because it matches on shape. Implementing any of them
would make the code worse.

- app/blocks/recommendation_template.py :: __missing__  — `dict.__missing__` returning `""` IS the format-map fallback; that is the feature.
- app/core/learning/router.py :: get_params  — sklearn estimator contract. This transformer has no hyperparameters, so `{}` is the accurate answer, not a placeholder.
- app/core/rag/vector_store.py :: close  — the store holds no per-instance resources. The engine is process-wide and cached in `app.core.db`; disposing it from one store would break every other store on the same URL. Kept for interface symmetry.
- app/routers/mcp.py :: mount_message_endpoint  — the `else` branch taken when `mcp[server]` is not installed. `return False` is the true report that nothing was mounted; the paired `/mcp/sse` route returns 503.
- scripts/rag_render_bulk_ingest.py :: fetch_existing  — `typing.Protocol` method. `...` is the idiomatic body.
- scripts/rag_render_bulk_ingest.py :: upsert_batch  — as above.
- scripts/rag_render_bulk_ingest.py :: count_chunks  — as above.
- scripts/rag_render_bulk_ingest.py :: upsert_documents  — as above.
- scripts/rag_render_bulk_ingest.py :: count_documents  — as above.
- scripts/rag_render_bulk_ingest.py :: wipe_all  — as above.

---

## Test harness — fakes in a dev script, never shipped

`scripts/audit_voice_block.py` builds a `_Probe` subclass to exercise the
voice block in isolation. These are its stubbed collaborators. No shipped path
imports this file.

- scripts/audit_voice_block.py :: _fetch_weather
- scripts/audit_voice_block.py :: _analyze_site_photo
- scripts/audit_voice_block.py :: _extract_activities_from_voice
- scripts/audit_voice_block.py :: _extract_issues_from_voice
- scripts/audit_voice_block.py :: _extract_equipment_from_photos
- scripts/audit_voice_block.py :: _extract_safety_observations
- scripts/audit_voice_block.py :: _extract_quality_observations
- scripts/audit_voice_block.py :: _extract_material_deliveries
- scripts/audit_voice_block.py :: _generate_daily_narrative
- scripts/audit_voice_block.py :: _generate_next_day_plan

---

## Recently closed

Not roadmap — implemented, listed so a reader can see the register moves in
both directions.

| was hollow | now |
|---|---|
| `construction :: _extract_tables_advanced` | Real table extraction via PyMuPDF `find_tables()`, with schedule classification (rebar / door / window / revision / legend). |
| `construction :: _extract_annotations` | Real PDF annotation layer extraction — the consultant markup trail. |
| `construction :: _extract_title_block` | Real label-anchored title-block reader: number, title, revision, scale, date, drawn/checked/approved, sheet, derived discipline. |
| `pdf_parser :: _blocks_to_tables` | Deleted. Replaced by `_tables_from_page`, which does real extraction — the old body returned `[]` for every PDF while a comment claimed pdfplumber handled it, and pdfplumber never ran. |
| drawing tables not reaching retrieval | Closed. `doc_index::_drawing_chunks_for_document` serialises recovered schedules and the title block into chunks on BOTH ingest paths, patterned on `_boq_chunks_for_document`. A schedule ROW is now retrievable as a row, not as loose words. |
| `containers/base :: get_rag_filters` | Deleted. Had exactly one reference in the repository: its own definition. |
| `routers/admin :: _legacy_sync_generate_unused` | Deleted. Dead legacy. |
