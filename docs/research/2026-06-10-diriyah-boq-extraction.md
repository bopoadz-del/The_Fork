# Diriyah BOQ extraction — research note

Source: Kimi research dispatch 2026-06-10 (`/tmp/kimi-fleet/20260610-011403-206/`).

## Hypothesis (ranked)

1. **Reading-order collapse in dense multi-column tables** — highest
   likelihood. PyMuPDF `get_text()` emits text in content-stream order,
   not visual reading order. BOQ columns (Item / Description / Unit /
   Qty / Rate / Amount) interleave, producing the "garbled chunks"
   symptom. Fix: **pdfplumber** with `extract_table()` + visual tolerances.

2. **Scanned image pages with no text layer** — high. Tender annexes,
   stamped approvals, faxed rate sheets. Our 30-char fallback threshold
   is brittle: a page with just header/footer text passes the gate and
   OCR is skipped. Fix: **Tesseract 5 via pytesseract, `lang='ara+eng'`**,
   render to 300 DPI.

3. **Lattice tables with ruled borders + merged cells** — moderate.
   PyMuPDF has no table semantics. Fix: **camelot-py Lattice mode**
   (OpenCV line detection, outputs pandas DataFrames).

## Two-pass strategy (mixed scanned + digital)

- **Pass 1 page triage**: `page.get_text("text")`; if >200 coherent
  chars AND contains BOQ keywords (Qty, Unit, Rate, SR), mark digital;
  else render to 300 DPI image and mark scanned.
- **Pass 2 extraction**: digital → pdfplumber (fallback camelot for
  heavily ruled sheets); scanned → Tesseract `ara+eng`. Normalize
  both into a unified markdown-table or CSV structure before chunking.

## Targets for a 9.7 MB BOQ

- Chunk count: 80-150 (chunk by work section, e.g. "Division 03 - Concrete")
- Average chunk length: 1000-2000 chars (~250-500 tokens)
- "Good" gate: >90% of chunks contain recognizable qty/item/unit triples,
  garbled-char rate <5%
- If <50 chunks OR avg <300 chars → over-fragmented, table structure
  destroyed.

## Acceptance criteria for a follow-up PR

- `app/core/doc_index.py` PDF branch gets a pdfplumber fallback that
  activates when PyMuPDF returns a chunk count <50 OR avg chunk length
  <300 chars
- Add `RAG_PDF_TABLE_FALLBACK=auto|on|off` env (default `auto`)
- Tesseract `ara+eng` only fires on pages with <30 text-layer chars
  (current behaviour kept for non-BOQ docs)
- Validate via the new `/v1/admin/debug/doc-extract` endpoint on
  Diriyah doc `c6dae280`: target 80+ chunks, avg >1000 chars
