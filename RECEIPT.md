# RECEIPT — extraction follow-through (R1 / R3 / R4 / R2 / R5)

## What landed

- **R1** `documents.superseded_by` + `documents.retrieval_visible` (default true).
  Hybrid BM25 **and** vector legs filter `retrieval_visible = true` (chunks
  without a documents row stay visible). Seed / ops helper sets
  `b5033ec2.superseded_by = 93982d45` and hides the stale row. No hard-delete.
- **R3** Ingest refuses a duplicate `content_sha256` unless `--reingest <old_id>`.
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

## Tests

Targeted pytest files under `tests/test_retrieval_visible.py`,
`tests/test_sha_reingest.py`, `tests/test_index_stamp_and_health_corpus.py`,
`tests/test_extraction_census.py` plus e2e `/health`.
