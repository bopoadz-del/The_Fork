# RAG Review — everything discussed, what's in, what's missing — 2026-09-12

Master record of the full RAG review. Verified against Neon (`ep-fragrant-river-a6dizxhm`)
and live `theshovel.ai`. Final retrievable chunk count: **124,166**.
This note lives in `docs/` (NOT `docs/knowledge/`) so it is not itself ingested as knowledge.

## A. Standards / reference knowledge (copyright-blocked → author summaries in curated_kb)

The full copyrighted standard PDFs are NOT and will not be ingested (licensed; can't serve).
Legitimate path = authored summaries in `docs/knowledge/` → `curated_kb` → general-knowledge merge.

| item | state | file |
|---|---|---|
| Saudi Building Code | **IN curated_kb** (real summary) | `docs/knowledge/ksa_saudi_building_code.md` |
| US design standards (IBC, IRC, IECC, ASCE 7, AISC 360, ACI 318, ASHRAE 90.1/62.1, NFPA 101/13/72/70/30/497, ASTM, ADA, OSHA 1910) — residential/commercial/industrial | **STUB committed, to fill + ingest** | `docs/knowledge/us_design_standards_residential_commercial_industrial.md` |
| UAE codes (Dubai Building Code, Al Sa'fat, ADIBC, Estidama Pearl, UAE Fire & Life Safety) | **STUB committed, to fill + ingest** | `docs/knowledge/uae_building_code.md` |

Also already in curated_kb: FIDIC (×3), OSHA 1926 (×6), CESMM4, WBDG (×8 — US design guidance),
rates (KSA/Gulf 2025), EVM, productivity norms. All INDEXED and reachable.

## B. Owned documents MISSING from the RAG (can ingest — yours, no license issue)

| item | count | where | note |
|---|---|---|---|
| Governance docs | 5 | Downloads (staged local) | MNL-204 PMWeb Manual for Contractor, PRC-203 Project Reporting, PRC-204 Document Control Management, Construction Submittal Form, Meeras Operational guidance |
| MGT message files | 3 | Master Folder root (online-only in Drive) | Message from MGT-C552-34 (Sect-1/2/3) — transmittals, separate from the SMGT submittal parts |
| DG2 text docs never ingested | ~134 | `DG2 Infra Pack 1` | mostly large drawing PDFs (ingest THIN — geometry, low value); the ~15-20 genuinely valuable are contracts (DD-2021-273 Executed Contract Jacobs), design reports, board outcomes, Media Phasing / Site Initiatives decks |

To ingest B: the worker (`the-fork-ingest`, has the bge embedder) into `drive_archive`, or a local
path once `sentence-transformers` is installed. Route: client docs → `drive_archive`; standards
summaries → `curated_kb`.

## C. Discussed but NOT owned / NOT available

- **AECOM rates** — not in the RAG and no rate schedule found on the drive. AECOM *correspondence*
  is in (1992 chunks). A rate schedule would need the actual file if it exists.

## D. Confirmed IN the RAG and reachable (verified live, 12/12 retrieval test)

SMGT-C552 parts 1–4 · the 4 Method Statements · MNL/PRC governance family · Project Controls
(49 indexed / 174 thin) · AECOM correspondence · risk register · baseline/critical-path · and the
curated standards summaries (SBC/FIDIC/OSHA/CESMM4/WBDG surface via general-knowledge merge).

## E. Excluded BY DESIGN — never ingested, not gaps

CAD (`.dwg/.dxf`), images (`.jpg/.png`), Google Earth (`.kmz/.kml`), video, GIS internals
(`.gdbtable/.spx/…`), fonts, and Arabic-named files. Only text formats produce retrievable chunks.

## F. Known limitations (not fixable by ingest)

- **1,710 thin docs** — drawing/CAD PDFs with no extractable text. No ingest thickens them.
- **curated_kb ranking** — 252 chunks vs drive_archive's 119,813; general-knowledge answers can be
  out-ranked unless the query is clearly general. Fix = boost GK weight, not re-ingest.

## Next actions (owner's call, none blocking)
1. Fill the US + UAE stubs → ingest into `curated_kb`.
2. Ingest the ~20 valuable owned text docs (governance + contracts + reports) via the worker.
3. Optionally retire the redundant `client_infra_pack_1` duplicate project.

*Reviewed 2026-09-12. Nothing was lost; the gaps above were never ingested, not deleted.*
