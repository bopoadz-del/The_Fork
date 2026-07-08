# Master Folder Ingestion Status Report

## Current State

- **Branch:** `feat/clean-rebuild-rag`
- **Target:** Ingest `G:/My Drive/Master Folder` (7,222 files) into RAG namespace `v2`
- **Embedder:** `BAAI/bge-small-en-v1.5`
- **Runner:** `scripts/p1b_run_batches.sh` — sequential 100-file batches
- **Active task:** `bash-7tzkkrr9`

## Progress

| Metric | Value |
|--------|-------|
| Total files in Master Folder | 7,222 |
| Already indexed (across prior attempts) | ~116 |
| Remaining filtered files | ~7,106 |
| Current batch | 0 (`manifests/p1b_master_folder_batch_000.json`) |
| Current file | ~6/7222 — `DD-2023-118_DG2 Infra P1_Vol 6 - Contractor's Proposal.pdf` |

## Extractors Added & Verified

| Format | Status | Evidence |
|--------|--------|----------|
| `.pptx` | ✅ Working | `NOC Tracker...` → 1,503 chars |
| `.kmz` | ✅ Working | `6. Metro Box.kmz` → 6,589 chars |
| `.zip` | ✅ Working (small/medium) | `OneDrive_1_6-23-2022.zip` → 84,011 chars |
| `.rar` | ✅ Graceful degrade on Windows | Returns `""` without unrar binary |
| `.msg` | ✅ Working | `240829 email from JM...` → 1,167 chars |
| `.doc` | ✅ Working | `Chadi_CV.doc` → 11,445 chars |

## Guards in Place

| Guard | Setting | Purpose |
|-------|---------|---------|
| `PDF_MAX_SIZE_MB` | 100 | Skip PDFs > 100 MB (avoid OOM on 446 MB drawing sets) |
| `PDF_OCR_MAX_SIZE_MB` | 25 | Disable OCR for PDFs > 25 MB, use text layer only |
| `P1B_MAX_FILE_SIZE_MB` | 100 | Skip copying files > 100 MB from Drive mount |
| Archive file size | 50 MB | Skip archives > 50 MB compressed |
| Archive total bytes | 50 MB | Limit uncompressed total per archive |
| Archive member count | 100 | Limit files extracted per archive |
| Archive image members | skipped | Avoid per-photo OCR/YOLO inside ZIPs |

## Issues Resolved

1. **First run died at ~124/7222** — likely OOM on large contract PDF.
2. **500-file batch killed by session close** at file 22.
3. **Batch runner killed silently** on 446 MB drawing PDFs.
4. **Batch runner killed again** on 36 MB scanned PDF OCR.

## Next Steps

- Allow `scripts/p1b_run_batches.sh` to complete all sequential batches.
- After completion, run P1c reconciliation: manifest expected vs indexed.
- Collect `skipped_too_large`, `zero_chunk`, and `errors` from batch reports.
