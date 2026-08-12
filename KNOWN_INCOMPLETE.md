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

These are split by WHAT BLOCKS THEM, because the two are not the same problem:

- **Data-blocked** — needs a source the platform does not have (manufacturer
  maintenance schedules, supplier part numbers, submitted bid documents).
  Writing code cannot close these.
- **Buildable now** — every input already exists and flows to the function;
  the mapping is simply unwritten. These are a scheduling decision, not a
  dependency.

The 2026-08-12 audit found the register over-blocking: five functions were
filed as data-blocked that are not. Being wrong in this direction is expensive
— it parks work that could ship, and it tells a client a capability is further
away than it is. Each claim below names the evidence.

### O&M manual — needs manufacturer maintenance data

- app/containers/construction/__init__.py :: _generate_daily_tasks  — Section E daily PM schedule; needs per-equipment manufacturer intervals.
- app/containers/construction/__init__.py :: _generate_weekly_tasks  — Section E weekly PM schedule; same source.
- app/containers/construction/__init__.py :: _generate_quarterly_tasks  — Section E quarterly PM schedule; same source.
- app/containers/construction/__init__.py :: _create_maintenance_matrix  — Section E responsibility matrix (task x equipment x trade).
- app/containers/construction/__init__.py :: _generate_troubleshooting_guide  — Section F; needs manufacturer fault codes and remedies.
- app/containers/construction/__init__.py :: _generate_spare_parts_list  — Section H; needs supplier part numbers and consumable intervals.
- app/containers/construction/__init__.py :: _extract_training_needs  — operator competency requirements per installed system.
- app/containers/construction/__init__.py :: _map_system_dependencies  — Section B interdependencies; needs a system topology model, not an equipment list.

### Daily site report from voice — needs speech input

- app/containers/construction/__init__.py :: _extract_material_deliveries  — deliveries from voice transcriptions. Its input is `transcriptions`, not photos (`schedule.py:493`), so no amount of vision work reaches it. Blocked on voice transcription quality for site audio, not on this function.

### Bid comparison — needs submitted bid documents

Both take a single `bid: Dict` and must judge it against tender requirements.
Neither can be derived from our own data: the input is a third party's
submission, which the platform has never been given.

- app/containers/construction/__init__.py :: _identify_qualification_gaps  — what a bid fails to qualify against the tender requirements.
- app/containers/construction/__init__.py :: _identify_bid_clarifications  — clarifications to raise with a bidder.

---

## Buildable now — blocked on code, not data

Every input these need already exists and already reaches them. They are in
this register because they still return empty, but they are NOT waiting on a
data source. Nothing external has to arrive first.

### Procurement analysis — the plan is our own data

All three take `procurement_plan` (and `scored_suppliers`), built at
`boq.py:1810-1833` from our BOQ and supplier records. Each plan item carries
`material`, `quantity`, `required_date`, `recommended_supplier`,
`supplier_score`, `order_date`, `order_lead_time`, `buffer_weeks`,
`packaging_strategy`, `inspection_required` and `alternative_suppliers`.

**The proof that this is sufficient:** `_generate_procurement_insights`
(`boq.py:1858`) has the SAME `(plan, suppliers)` signature, sits twelve lines
above `_identify_procurement_risks`, and is fully implemented — it already
derives long-lead exposure (`order_lead_time > 8`), single-source dependency
(`alternative_suppliers == []`) and weak-supplier warnings (`avg score < 75`).
It returns them as prose strings. The register previously claimed its immediate
neighbour needed a bid corpus for the same inputs; that was wrong.

- app/containers/construction/__init__.py :: _identify_procurement_risks  — single-source and long-lead risk on a procurement plan. Buildable now: return the structured form of what _generate_procurement_insights already computes as prose, plus order_date already past, and inspection_required items with no lead-time buffer.
- app/containers/construction/__init__.py :: _identify_consolidation  — procurement packages worth consolidating. Buildable now: group plan items by `material` and by `recommended_supplier`; repeats across BOQ items are the consolidation candidates.
- app/containers/construction/__init__.py :: _suggest_bundling  — supplier bundling opportunities. Buildable now: group by `recommended_supplier` within a `required_date` window, cross-checked against `scored_suppliers` capability; `packaging_strategy` already flags bulk-eligible lines.

### Two construction actions call helpers that were never written

`docs/archive/HANDOFF.md` and `docs/archive/PROGRESS.md` park these two as
"real, parked-by-design — need multi-file resolution". **That reason is
wrong.** They do not need multi-file resolution. They cannot run at all:
between them they call four helpers that have never been defined anywhere in
this repository's history. `git log -S "def <helper>" --all` returns nothing
for all four.

    generate_construction_report -> self._generate_doc_recommendations
    track_progress               -> self._assess_delay_risk
      (via _compare_photo_to_bim -> self._element_similarity,
                                 -> self._find_deviations)

`generate_construction_report` additionally reads five keys off
`process_document` (`doc_type`, `detected_disciplines`, `total_pages`,
`measurements`, `tables`) that its current contract does not return — it was
written against an older version of that function.

Measured 2026-08-12 by dispatching each through `route()`:

    track_progress                 -> AttributeError: _assess_delay_risk
    track_progress (with photos)   -> AttributeError: _find_deviations
    generate_construction_report   -> KeyError: 'doc_type'

They are deliberately NOT in the handlers table. Routing them replaces an
honest `"Unknown action"` refusal with an unhandled 500, which is worse than
the gap. `tests/test_construction_actions_reachable.py` holds both properties:
a new phantom helper fails immediately, and no unrunnable method may ever be
routed (that rule has no exceptions). The code is kept rather than deleted so
the near-miss survives for whoever writes the helpers.

Not "buildable now" in the sense the rest of this section means: the four
helpers are domain logic to design, not wiring to restore — an element
similarity metric, a delay-risk heuristic, a deviation classifier, and a
document recommendation generator. Inventing them to make the tests pass would
be fabricating capability.

- app/containers/construction/__init__.py :: track_progress  — as-built photo vs BIM progress comparison. Needs `_assess_delay_risk`, and `_compare_photo_to_bim` needs `_element_similarity` + `_find_deviations`. Note `progress_tracker` (schedule.py) is a DIFFERENT, working capability — EVM/variance from percentages — not a replacement for this one.
- app/containers/construction/__init__.py :: generate_construction_report  — formal report over a parsed document. Needs `_generate_doc_recommendations`, and its read of `process_document` must be re-based on that function's current keys.

### Site photo intelligence — BUILT 2026-08-12

`_extract_equipment_from_photos` and `_extract_quality_observations` are
implemented and wired; they are no longer registered here. The register's
previous reason — "needs the vision model wired through" — was wrong twice
over: the weights were committed at `data/models/safety_world_v2.onnx` all
along, and the daily-report path already called all three consumers. What was
actually missing was that the producer ran the generic `image` block instead of
the construction detector, so the 33-prompt vocabulary never executed.

Measured on 21 real photographs in `docs/PHOTO_INTELLIGENCE_EVAL.md`:
**quality 9% at the 0.30 reporting threshold, plant 60%.** Usable as a prompt
for a human to review specific photos; NOT a QA record. What remains is
registered below as vocabulary and model limits, not as unbuilt code.

---

## Stated limitations — implemented, but narrower than it looks

**Site-photo observations detect far less than the field names suggest.**
Measured 2026-08-12 over 21 real construction photographs
(`docs/PHOTO_INTELLIGENCE_EVAL.md`): **9% recall on quality/workmanship** at the
0.30 reporting threshold, **60% on plant** over a three-class vocabulary.

Three separate limits, none fixable in the bridge code:

- **Vocabulary gap — plant.** The baked vocabulary has exactly three plant /
  temporary-works prompts: crane, ladder, scaffolding. Excavators,
  telehandlers, dumpers, piling rigs and concrete pumps have NO prompt string
  and are undetectable at any confidence. The equipment section is not padded
  to hide this. Fixing it means adding prompts and re-baking the ONNX.
- **Vocabulary gap — quality precision.** `exposed aggregate in concrete
  surface` and `water stain on wall` behave as near-catch-alls for concrete
  texture: they fired at 0.58-0.63 on photos containing neither. False
  positives reach the same reporting tier as true ones.
- **Model limitation — surface defects.** Cracks, peeling paint and
  honeycombing are largely missed, while whole objects (ladder 0.962, helmet
  0.989) are strong. Consistent with an open-vocabulary detector grounded on
  object nouns rather than material-condition textures; likely needs a
  different model class, not more prompts.

Consequence for use: the quality section is a prompt to look at specific
photographs, not a QA finding. Every observation carries its source photo and
confidence so a reader can check it, and nothing in the pipeline may call
anything a defect, violation or non-compliance.


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
