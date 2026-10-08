# Handover note — TASK F-UI-PASS (2026-10-08)

Read the order first: it is in the owner's message ("TASK F-UI-PASS (continue)"). It is binding: **no hardwiring**, nothing counts as passed unless it was seen in the UI, no load or capacity runs until the pass is clean, and never print secrets.

## Live state
- Live: main at the #851 merge (`fd69819`), healthy. Task def rev 8 (`DRIVER_MODE=request`).
- Driver mode is on for the test user through the `users.driver_mode` flag (admin switch: `/v1/admin/driver-mode`, Admin page).
- **Open PR #852** `feat/plain-display-names`. Every formula (87) and tool (16) now declares a `display_name`, and credits render through `app/lib/source_labels.py`. CI was running at handover. Merge when green, deploy, then check `/health`.
  - The local failure in `test_leftover_hat_ui_fails::test_construction_container_aliases_bim_extractor_action` is only because ifcopenshell isn't installed locally.

## UI results so far (one user, Master Corpus project)
- **Old path, formulas:**
  - Pass: commercial, contracts, planning, procurement, qaqc, quantities, safety.
  - Base: fixed in #847, still needs a UI re-run.
  - Design: fails. The model computed 8.33 mm; the correct answer is 2.08 mm.
- **Driver mode, formulas:**
  - Pass: base, design, contracts (50,000/day) and planning (TF 4, not critical).
  - Commercial: passes after #851 (net 2,160,000). The Sources panel shows 5 documents and the "Calculated with" line appears.
  - Still to run: procurement, qaqc, quantities, safety.
- **Driver first token, one user:** 17.8 s before, about 6–6.75 s after.
- **Screenshots:** local only, never committed. Several items are "text only" because the Chrome window was hidden.

## Failures found, not yet fixed (each is the next task)
1. **A citation can't always be opened or downloaded.**
   - 3 of 5 cited documents have no stored original. The preview returns 404 ("no Google Drive file id"); the UI does show that message.
   - There is **no download endpoint at all**, even though `DocumentPreview.tsx:144` says "use the document list to download it".
   - Planned fix (started, discarded unfinished):
     - in `app/routers/projects.py`, add `allow_missing_file` to `_resolve_preview_document`;
     - add `_indexed_text(doc_id)`, which reads `chunks.text` ordered by `chunk_index`;
     - when no file is stored, the preview falls back to `{"kind":"text","indexed_only":true,"note":...}`;
     - new `GET /v1/projects/{pid}/documents/{doc_id}/download` returns the original bytes, or "<name> (indexed text).txt", with the filename scrubbed for other projects' layers;
     - frontend: a Download button in `DocumentPreview`;
     - write tests for all of it.
2. **`RAG_SCRUB_RULES` (secret) is a client denylist:** 11 rules, all proper names. Per the order it must go.
   - Planned structural replacement in `app/core/identifier_scrub.py`:
     - the registered `projects.name/client/location` of every project whose documents are served to other projects (`MASTER_CORPUS_SOURCE_PROJECT_ID`, `RAG_GENERAL_KNOWLEDGE_PROJECTS`), using the variants and acronyms in `party_names._variants/_acronym`;
     - document-number codes that recur in one project's filenames and no other's;
     - title-block "Project Name:" labels.
   - Parties are already structural (`app/core/party_names.py`).
   - The coverage check against the live secret and DB (counts only) was **blocked by the local permission classifier**; the cloud agent or the owner must run it.
   - Then remove the secret ref from the task def, the same way as DRAWING_QTO_EXCLUDED_PLACE_NAMES, but only when no test run is in progress.
3. **Old path:** the design arithmetic is wrong, and the pre-dispatch commentary leaks ("file you flagged", tool names).
4. **Left panel:** Master Corpus documents show "Not indexed" while answers report 3293 of 3352 indexed. Not investigated.
5. **Improvised-formula label and gap log:** not implemented.
6. **Re-run planning in driver mode** (#850 is live) to confirm the line break before "Criticality:".

## Still to do (order step 6)
- Tools: every tool of every hat, asked naturally, on both paths.
- Documents: a disagreement between two documents, a codes question, a missing input, an uncovered subject.
- Uploads: an accepted type, a zip, a DWG, a photo.
- Deliverables: every predefined workflow, opening the file it produces.
- Then the same list on the old path, then the final report (20 lines max).

## Rules carried over
- Merge one PR at a time: green → merge → deploy → `/health`.
- Don't touch other sessions' PRs (#794–#797, agent-s branches).
- Never read `~/.thefork-backup/UI-PHYS_DG2_SET3_unseen.csv` or `unseen_set_v1.py`.
- Owner-side files are never committed.
- No env changes on live during a test run.
- No resource changes.
