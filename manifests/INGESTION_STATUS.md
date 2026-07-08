# Master Folder Ingestion Status Report

## Current State

- **Branch:** `feat/clean-rebuild-rag`
- **Target:** Ingest `G:/My Drive/Master Folder` (7,222 files) into RAG namespace `v2`
- **Embedder:** `BAAI/bge-small-en-v1.5`
- **Runner:** `scripts/p1b_run_batches.sh` — sequential **1,000-file batches** (was 100)
- **Active task:** `bash-4ad9vim8` (replaced `bash-5bmlf5ig` for larger batches)

## Progress

| Metric | Value |
|--------|-------|
| Total files in Master Folder | 7,222 |
| Already indexed (across prior attempts) | ~119 |
| Remaining filtered files | ~7,103 |
| Current batch | 0 (`manifests/p1b_master_folder_batch_000.json`) |
| Current file | ~11/7222 — batch 0 in progress |
| Batch 000 status | 6 succeeded, 3 unsupported skipped, 0 errors |

## Latest Patch (2026-07-08)

`scripts/p1b_ingest_local_folder.py` now skips the following **before** attempting to read/copy from the Google Drive mount:

| Category | Extension | Result |
|----------|-----------|--------|
| Google Workspace native files | `.gdoc`, `.gsheet`, `.gslides`, `.gdraw` | `SKIPPED_UNSUPPORTED` |
| RAR archives (cannot be read from mount) | `.rar` | `SKIPPED_UNSUPPORTED` |
| Files that fail `stat`/`read_bytes` on the mount | any | `SKIPPED_UNREADABLE` |

This fixes the `OSError: [Errno 22] Invalid argument` pattern seen on:

- `Master Folder/.archivetempES Qld Trust Employee Information forms (1).gdoc`
- `Master Folder/Chadi_CV PM.gdoc`
- `Master Folder/Copy of CAD.rar`
- `Master Folder/DG2 Infra Pack 1/Contract Docs/.../DD-2023-118_DG2 Infra P1_Vol 3 – Drawings (4-6 of 7).pdf`
- `Master Folder/DG2 Infra Pack 1/Contract Docs/.../DD-2023-118_DG2 Infra P1_Vol 4 - Schedule (1-2 of 2) BOQ.pdf`

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
5. **Embedder reload overhead** — switched from 100-file to 1,000-file batches so `BAAI/bge-small-en-v1.5` loads ~7 times instead of ~70. Resilience preserved via `--resume`.

## Next Steps

- Allow `scripts/p1b_run_batches.sh` to complete all sequential batches.
- After completion, run P1c reconciliation: manifest expected vs indexed.
- Collect `skipped_too_large`, `zero_chunk`, and `errors` from batch reports.
