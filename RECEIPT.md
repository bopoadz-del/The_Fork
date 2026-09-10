# RECEIPT — extraction follow-through (R1 / R3 / R4 / R2 / R5)

## What landed

- **R1** `documents.superseded_by` + `documents.retrieval_visible` (default true).
  Hybrid BM25 **and** vector legs filter `retrieval_visible = true` (chunks
  without a documents row stay visible). Seed / ops helper sets
  `b5033ec2.superseded_by = 93982d45` and hides the stale row. No hard-delete.
- **R3** Ingest refuses a duplicate `content_sha256` unless `--reingest <old_id>`
  (scoped to that row's id/sha so a folder walk cannot hide one id behind
  every other file). Admin / CDE / reconcile skip instead of abort.
- **R4** `doc-reindex` and `--reingest` stamp `chunk_count`, `ingest_status=INDEXED`,
  `extractor_version=8535199-sdt`. `/health` corpus `chunks` is `COUNT(*)` on the
  chunk table (`source=chunk_table_count`), not `SUM(documents.chunk_count)`.
- **R2** `scripts/extraction_census.py` + fixture test (`delta==0` after a
  correct index). Live census is ops-run after merge.
- **R5** `8199b14b` origin (see below).

## R5 — 8199b14b

`8199b14b` is a fabricated `LETTER_DOC` fixture in
`tests/test_letter_filename_retrieval.py` (and comments in
`app/core/rag/retriever.py`); repo search + GitHub `8199b14b` hits are only those
two files. Comments already state the id is MISSING from `documents`. That is a
**FORK_EVAL citation-grounding failure**: citations must resolve against
`documents` before render. Live sparse extract is `b5033ec2`; the corrected
copy id `93982d45` is Neon-only and is seeded when both rows exist. Do not
treat `8199b14b` as a real document id.

## mutants run/survivors

UNPRODUCED

## Rebase onto main 234d95ad (#557) — what broke

GitHub check **client-pattern scan** (job 102858412464) went red after
update-branch. `scripts/scan_secrets.py` itself was clean
(`8 pattern(s), 1568 file(s)`). The same job then runs
`scripts/scan_exception_pass.py` — #557's silent-empty-return twin — and
that step flagged 16 `file:line` keys.

Those 16 were not new swallows. R1–R5 inserted lines in `projects.py`,
`doc_index.py`, `retriever.py`, `vector_store.py`, and
`p1b_ingest_drive_server.py`, so the 124-entry `RETURN_ALLOWLIST` from
#557 pointed at stale line numbers. Live site count stayed 124.

Minimal fix: retarget the 16 drifted keys via
`python scripts/scan_exception_pass.py --list-returns`. Ceiling stays 124.
R1–R5 unchanged.

## Lint gates

- `scripts/audit_stubs.py` — clean
- `scripts/scan_exception_pass.py` — clean (`RETURN: 0 new (124 baselined)`)
- `frontend` eslint — clean
- `scripts/scan_secrets.py` — CI-clean on 3ea31a06; not re-run here
  (`SECRET_SCAN_PATTERNS` unset)

## Tests

Targeted + related (this revision):

- `tests/test_retrieval_visible.py` `test_sha_reingest.py`
  `test_index_stamp_and_health_corpus.py` `test_extraction_census.py`
  `test_projects_migration.py` `test_p1b_ingest_drive_server.py`
  `test_health.py` `tests/e2e/test_f1_boot_and_health.py`
  `test_health_capability_probe.py` `test_hybrid_retrieval.py`
  `test_doc_index_zero_chunk.py` — 68 passed, 4 skipped
- `tests/e2e/` + p1b accounting / silent_exit / r2 / drive proof /
  letter filename — 129 passed, 1 skipped

mutants run/survivors: UNPRODUCED

HEAD: `a6c1a24bec5a8e4d00876b3b05dcdace068a37a8`
retries: 2 (reload class identity; --reingest scope after review)
