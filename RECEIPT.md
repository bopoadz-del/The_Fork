# RECEIPT — S10 ingest reconcile (Gate-2 prep)

Audit of **current** tip (`5eaf4d8` / #578) plus this delta. F-3 (159
docx source bytes) remains HELD / owner-gated. This PR does **not** run
`p1b --tier 1`, does **not** claim Gate-2 corpus done, does **not**
purge orphans.

## Already true (struck — do not rebuild)

| Claim | Evidence |
| --- | --- |
| `documents.superseded_by` + `retrieval_visible`; hybrid hides invisible | `app/core/models.py`, `vector_store._hidden_doc_sql`, #551 / RECEIPT R1 |
| Per-doc `ingest_status` / `chunk_count` / `drive_md5` **columns** | migration `0016`, `Document` ORM. `drive_md5` was unused until this PR |
| Duplicate SHA refused unless `--reingest` | `projects.add_document` / `DuplicateContentError`, #551 |
| INDEXED stamp + zero-chunk is `ZERO_CHUNK` not silent INDEXED | `ingest_status.classify`, `doc_index._stamp_index_ledger`, `test_doc_index_zero_chunk.py` |
| Stale-docx reextract + stamp-guards | #569–#577, `resume_is_already_indexed` |
| Orphan chunks **reported** (no purge) | `POST /v1/admin/corpus/reconcile` `dangling_*` |
| JSONL bulk path re-embeds on model/dim mismatch | `scripts/rag_render_bulk_ingest.py` `is_valid_existing` |
| `/health` corpus = `COUNT(*)` on chunk table | `health_probes.probe_corpus_chunks` |
| S14 EOT notice-period | closed #561 — out of scope |

## Holes that were still real (this PR)

1. **Resume by Drive id only** — `drive_md5` never written, never in
   `_document_as_dict`, Drive walk omitted `md5Checksum`/`etag`. Edited
   files with the same id were skipped. Now: persist token on ingest;
   `should_skip_resume` compares md5/etag when **both** sides exist.
   Null stored token is **not** treated as changed (would re-index the
   whole historical corpus / F-3 thrash).
2. **Drive deletions never tombstoned** — `TOMBSTONED` existed in the
   CHECK vocab with no writer. `reconcile_drive_delta` only imported
   missing files. Now: complete-walk tombstone = `TOMBSTONED` +
   `retrieval_visible=false`. Never deletes chunks. Partial walk / p1b
   shards do **not** tombstone.
3. **OCR skip not on the ledger** — `ocr_skipped_too_large` lived in
   extract meta; `OCR_REQUIRED` returned without `_stamp_index_ledger`.
   Now stamps `ingest_status_reason` containing `OCR_DEGRADED` (reason
   token, not a new CHECK status).
4. **Coverage not queryable as ingest truth** — N-of-M honesty + health
   chunk count existed; no status / tombstone / OCR / orphan /
   embedding-mismatch breakdown. Now `GET /v1/admin/corpus/coverage`
   and `coverage_truth()`.
5. **Idempotent reconcile** — `plan_reconcile` second pass on a healthy
   snapshot has `index_count==0` / `work_count==0`.

## Still blocked (not a code hole)

- F-3: 159 docx need owner-gated source bytes. Do not run Shell p1b
  theater. Gate-2 corpus is **not** done.
- Orphan **quarantine** of retrieval is left as report-only: R1
  explicitly keeps chunks without a `documents` row visible. Closure is
  owner-gated purge, not this PR.
- Force re-embed of a live mixed-model corpus is listed on the plan
  (`to_reembed`) and counted in coverage; the JSONL path already
  re-embeds. Auto-reindex of the production corpus is out of scope.

## Tests

Targeted (this revision, local):

- `test_ingest_reconcile` + `test_p1b_ingest_accounting` (incl. md5
  resume) + `test_projects_migration` + `test_admin_corpus_reconcile`
  + `test_ingest_status` + `test_doc_index_zero_chunk` — **74 passed**
- Related: `test_p1b_ingest_accounting` full + `test_doc_index_ocr` +
  `test_sha_reingest` + `test_retrieval_visible` +
  `tests/e2e/test_f1_boot_and_health` — **51 passed, 1 skipped**
- `scripts/audit_stubs.py` — clean
- `scripts/scan_exception_pass.py` — `RETURN: 0 new (77 baselined)`

One unrelated local e2e (`test_chain_mcp_feedback_memory_hydration_usage_workflows_schedule_rag`)
503'd because this VM cannot load `BAAI/bge-small-en-v1.5` (SafetensorError).
Not caused by S10; CI uses the job embedder.

mutants run/survivors: UNPRODUCED
retries: 0
