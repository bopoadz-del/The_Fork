# RECEIPT — S11 office extraction census (Gate-2 prep)

Audit of tip `897df10` / #579 (S10) plus this delta. F-3 (159 docx
source bytes) remains HELD / owner-gated. This PR does **not** run
`p1b`, does **not** thrash `reextract_stale_docx`, does **not** claim
Gate-2 corpus done.

## Already true (struck — do not rebuild)

| Claim | Evidence |
| --- | --- |
| docx content-control census + `--apply-reingest` | `scripts/extraction_census.py`, #550 / #551 |
| Zero-chunk is `ZERO_CHUNK`, not silent INDEXED, on first ingest | `ingest_status.classify`, `doc_index._stamp_index_ledger` |
| PDF OCR skip / cover-page / batched extract | `_extract_pdf`, `_scanned_pdf_missing_ocr`, S10 `OCR_DEGRADED` |
| XLSX row-wise priced-BOQ extract | `_extract_with_meta_impl` `.xlsx`, `test_extract_xlsx_keeps_priced_boq_row_intact` |
| PPTX slide titles + text boxes | `_extract_pptx`, `tests/test_pptx_extraction.py` |
| Corpus-wide status histogram | S10 `coverage_truth` / `GET /v1/admin/corpus/coverage` — **not** by kind |
| S10 Drive md5 / tombstones / OCR_DEGRADED | #579 |
| S13 master-corpus search 404s | #578 |
| S14 EOT notice-period | #561 — out of scope |

## Holes that were still real (this PR)

1. **No pptx / xlsx / pdf census** — S10 `coverage_truth` grouped the
   whole ledger. `scripts/extraction_census.py` is docx-only. Now
   `tally_office_docs` / `office_extraction_census` and `office` on
   `GET /v1/admin/corpus/coverage`, plus
   `scripts/office_extraction_census.py`. Tallies by kind: status,
   single-chunk, thin (TEXT_SPARSE or 1 chunk), missing source,
   `indexed_zero_chunk`, `extractor_version`.
2. **PPTX tables never extracted** — `shape.text` skips GraphicFrame
   tables. NOC / register decks looked thin. `_pptx_shape_texts` walks
   table rows.
3. **Corrupt PPTX looked empty** — `_extract_pptx` swallowed every
   exception as `""`, so `_extract_with_meta_impl` never set
   `extract_failed`. Index stamped `ZERO_CHUNK` instead of
   `EXTRACT_FAILED`. Now `_extract_pptx_with_meta` names the failure.
4. **Corrupt PDF looked empty** — `_extract_pdf` returned `("", {})`
   on a generic open error. Meta now carries `extract_failed`.

## Still blocked (not a code hole)

- F-3: 159 docx need owner-gated source bytes. Do not run Shell p1b
  theater. Gate-2 corpus is **not** done.
- Live office tallies stay UNPRODUCED until ops runs the read-only
  script against production `DATA_DIR`.
- S12 / S16 remain paused.

## Tests / tools

- tests passed/failed: local targeted 60 passed (`test_office_extraction_census`, `test_pptx_extraction`, `test_ingest_reconcile`, `test_admin_corpus_reconcile`, `test_oom_not_reported_as_empty`, xlsx extract, `test_doc_index_zero_chunk`). CI virgin / production-like / test-postgres pending.
- mutants run/survivors: UNPRODUCED
- retries: 0
- tools used: pytest, `scripts/audit_stubs.py`, `scripts/scan_exception_pass.py` (RETURN 0 new, 76 baselined after pptx empty-return closed)
- rollback SHA: `897df10`
- deploy SHA: (this branch HEAD)
