# Photo RAG with Safety + QA/QC Detection — Design Spec

**Date:** 2026-06-24
**Status:** Brainstormed, awaiting operator review before plan
**Author:** Claude (via brainstorming skill, in collaboration with operator)

## Goal

Add construction site photos to The Fork's RAG so the platform can retrieve them by their visual content. V1 focus: detect six common safety violations and QA/QC defects in photos using a fine-tuned YOLOv8 model trained on the `construction-3-001.zip` corpus (208 files), then expose the resulting bbox + class metadata as first-class RAG chunks served by Render.

## Non-Goals (V1)

- Live video / camera streams
- Relational / multi-entity rules (water-next-to-cable, opening-not-closed-by-plywood)
- Subjective "quality degree" classes (uneven plaster, dirty false ceiling)
- Active-learning loop with operator-correction feedback
- Built-in labelling UI inside the platform
- Auto-redeploy of new fine-tuned weights to Render
- Project-context inference from photo visuals (NEVER — see `feedback-no-assumptions.md`)
- Vector embeddings for photo chunks (BM25 over caption + class names for V1)

## Architecture

**Two-host split:**

- **Local PC (operator's workstation, GPU available):** all training, all labelling, all batch inference. The PC owns the build pipeline end-to-end. The PC is NOT a runtime dependency — Render must function with the PC offline.
- **Render (cloud, CPU-only, srv-d8hdc6ek1jcs739rq5sg):** holds the produced metadata in Postgres, serves it as RAG chunks alongside existing document chunks. Never runs vision inference in V1.

**Connection:** one-shot HTTPS POST from PC to a new admin endpoint on Render after each training cycle.

**Why this split:** Render starter is $7/mo CPU-only; YOLO inference is ~100 ms/photo CPU and ~10 ms/photo GPU. No tunnel + no GPU on Render = clean and cheap. Operator's prior pushback against PC-tunnel dependencies (`feedback-agents-via-ollama` notwithstanding — that's LLM-specific) drove this choice. Phase 3 (live upload pathway) can revisit if real-time matters.

## Components (new)

| Component | Location | Purpose |
|---|---|---|
| `safety_classes.json` | `app/blocks/` | Class registry. All ~33 classes from operator's taxonomy. Each entry: `id` (int, stable), `name` (str), `category` (`safety`/`qaqc`), `definition` (str), `active` (bool), `weights_version` (str?), `min_examples_required` (int, default 30). V1 marks 6–10 active; rest are placeholders. |
| `survey_photo_corpus.py` | `scripts/` | Phase 0. Runs Grounding DINO (open-vocab detector) over a photo folder with class names as prompts. Outputs `data/training/photo_survey.json` with per-class detection counts. |
| `prelabel_with_dino.py` | `scripts/` | Phase 1a. Runs Grounding DINO on V1-active classes, exports Label Studio JSON for operator correction. |
| `train_safety_qaqc.py` | `scripts/` | Phase 1c. Thin wrapper over `ultralytics.YOLO.train()`. 80/20 split, fixed seed. Outputs `data/models/safety_qaqc_v{N}.pt` + `eval_v{N}.json` (mAP per class). |
| `infer_photo_metadata.py` | `scripts/` | Phase 2a. Runs PIL metadata + Tesseract OCR + COCO-YOLO (existing image.py) + fine-tuned safety/QA-QC YOLO on every photo. Outputs `data/training/photo_metadata.jsonl`. |
| `export_to_render.py` | `scripts/` | Phase 2b. Streams JSONL to Render admin endpoint with bearer auth, idempotent on SHA-256. |
| `app/blocks/safety_detector.py` | new module | Loads + serves the fine-tuned YOLO. Used by `infer_photo_metadata.py` on PC; registered in the block registry but unused at runtime in V1 (Phase 3 activation). |
| `app/routers/admin_photos.py` | new router | `POST /v1/admin/photo-import`: accepts JSONL of photo metadata, idempotent. Admin-auth gated. |
| Alembic migration `0006_doc_index_photo_metadata.py` | `alembic/versions/` | Adds `kind TEXT NOT NULL DEFAULT 'text'` to `doc_index`. Adds nullable `photo_metadata JSONB`. Creates new `photos` table (`sha256` PK, `content_type`, `size_bytes`, `bytes BYTEA`, `uploaded_at`). SQLite + Postgres branches. |

## Components (extended)

- **`app/blocks/image.py`** — gains a `safety_qaqc` mode that delegates to `safety_detector`. Returns existing PIL + Tesseract + COCO output PLUS the fine-tuned model's bbox + class output. Uses the canonical `file_path` input key (the construction container's call sites were renamed from the legacy `image_path` key in a separate concurrent change — `safety_detector` must follow the same contract).
- **`app/containers/construction/__init__.py` and `documents.py`** — existing methods `safety_compliance_audit()` and `qa_qc_inspection()` already convert image-block output into hazard / defect lists with severity. Those methods stay. The new `safety_detector` produces typed class labels (e.g. `no_hardhat`) that compose into the same hazard / defect format these methods consume, replacing the brittle keyword-greping path on description text. Net effect: stronger detection signal flowing into the same downstream verdict logic.
- **`app/core/rag/retriever.py`** — when retrieving, `kind="photo"` chunks have a `content` field built from caption + class label names; citation includes original photo URL + thumbnail. No retriever rewrite; just a content-source adapter.

## Data Flow

```
construction-3-001.zip
        |
        v  extract (Phase 0a)
data/training/raw_photos/
        |
        v  survey_photo_corpus.py        (Phase 0b)
data/training/photo_survey.json          ← per-class counts; locks V1 list
        |
        v  prelabel_with_dino.py         (Phase 1a, V1-active classes only)
data/training/labels_draft/              (Label Studio JSON)
        |
        v  operator corrects in Label Studio  (Phase 1b, manual)
data/training/labels_final/              (YOLO txt + images, 80/20 split)
        |
        v  train_safety_qaqc.py          (Phase 1c)
data/models/safety_qaqc_v1.pt
data/training/eval_v1.json
        |
        v  infer_photo_metadata.py       (Phase 2a)
data/training/photo_metadata.jsonl
        |
        v  export_to_render.py           (Phase 2b, HTTPS POST)
Render Postgres: doc_index rows (kind="photo")
        |
        v  RAG retrieval                 (Phase 2c, automatic)
queries matching class names return photo chunks with bbox metadata
```

### `photo_metadata.jsonl` row schema

```json
{
  "sha256": "abc123...",
  "filename": "IMG-20230523-WA0009.jpg",
  "source_zip": "construction-3-001.zip",
  "project_id": null,
  "pil": {
    "width": 4160,
    "height": 2336,
    "format": "JPEG",
    "file_size_bytes": 2451234,
    "dominant_channel": "red"
  },
  "ocr_text": "(empty or extracted text)",
  "coco_objects": [
    {"class": "person", "confidence": 0.91, "bbox": [x, y, w, h]}
  ],
  "safety_qaqc": [
    {"class_id": 0, "class": "no_hardhat", "confidence": 0.87, "bbox": [x, y, w, h], "category": "safety"},
    {"class_id": 4, "class": "concrete_honeycomb", "confidence": 0.74, "bbox": [x, y, w, h], "category": "qaqc"}
  ],
  "caption": "Site photo showing 1 worker without hard hat; concrete surface with honeycomb defect.",
  "inference_failed": false,
  "inference_error": null
}
```

**Why `project_id: null` for V1:** the zip has no confirmed project (operator hasn't bound it to one). Forcing a project_id would violate `feedback-no-assumptions.md`. Phase 3 populates this field from the active project when photos are uploaded through the platform UI.

**Caption generation:** templated from class labels + counts. Not a VLM call. Example template: `"Site photo showing {count_safety} safety issue(s): {safety_list}; {count_qaqc} QA/QC issue(s): {qaqc_list}."` If both lists are empty, caption is `"Site photo (no detected violations or defects)."`.

## Class Registry — V1 Activation Rules

`safety_classes.json` ships with all ~33 classes from the operator's taxonomy. Class IDs are **stable forever** — once assigned, an ID never gets reassigned even if a class is deprecated. The YOLO model only sees `active=true` classes; the active subset is renumbered 0..N-1 at training time via a class-id remap (stored in `data/models/safety_qaqc_v{N}_classmap.json`).

**Activation gate:** a class becomes `active=true` when survey shows ≥30 example bboxes. Below 30, the class stays in the registry as a placeholder; inference returns no detections for it.

**V1 active candidates (subject to survey confirmation):**

| ID | Name | Category |
|---|---|---|
| 0 | `no_hardhat` | safety |
| 1 | `no_high_vis_vest` | safety |
| 2 | `fall_hazard_unprotected` | safety |
| 3 | `concrete_crack` | qaqc |
| 4 | `concrete_honeycomb` | qaqc |
| 5 | `rebar_correct_inspection` | qaqc |
| 6 | `rebar_exposed_defect` | qaqc |

**Placeholder classes (active=false at V1):** all remaining 26 items from the operator's expanded list, IDs 7..32 reserved. Examples: `excavation_slope_unsafe`, `loose_sand_edge`, `missing_jersey_barrier`, `missing_lifeline`, `non_safety_shoes`, `faulty_scaffolding`, `missing_toe_board`, `no_scaffold_green_tag`, `unorganized_cables`, `cables_no_trunking`, `water_near_cable`, `ceiling_opening_unsealed`, `shaft_opening_unsealed`, `rebar_no_plastic_cap`, `uneven_tile_grouting`, `bulging_concrete`, `uneven_plaster`, `dirty_false_ceiling`, `uncentered_electrical_socket`, `no_chamfer_on_edge`, `column_misalignment`, `kerb_misalignment`, `dirty_pre_cast_surface`, `dirty_finished_room`. Final names + definitions locked during Phase 0 survey.

**Note on relational classes:** `water_near_cable`, `cables_no_trunking`, `ceiling_opening_unsealed`, `shaft_opening_unsealed` involve spatial reasoning between two or more detected objects. V1 keeps these as registered names only; the spec for activating them requires a separate predicate-rule layer on top of YOLO (out of scope for V1).

## RAG Integration on Render

**Migration `0006`:**
- `ALTER TABLE doc_index ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'`
- `ALTER TABLE doc_index ADD COLUMN photo_metadata JSONB` (Postgres) / `TEXT` (SQLite, JSON-encoded)
- Backfill: existing rows get `kind='text'` via the DEFAULT.

**Two endpoints (used in sequence by `export_to_render.py`):**

1. `POST /v1/admin/photo-bytes/{sha256}` — multipart upload. Stores the raw photo bytes in the `photos` table. Idempotent: existing `sha256` returns 200 without re-writing. Body limit: 25 MB per photo.
2. `POST /v1/admin/photo-import` — JSONL metadata.
   - Auth: bearer token, admin role only (uses existing admin middleware).
   - Body: JSONL stream of `photo_metadata` rows.
   - Behavior: idempotent on `sha256`. New rows inserted with `kind='photo'`, `content` = templated caption, `photo_metadata` = full JSON blob. Existing rows with same `sha256` are skipped (no overwrite — operator must explicitly delete + re-import to update). Rejects rows whose `sha256` has no corresponding bytes in the `photos` table (ordering enforced).
   - Response: `{inserted: N, skipped_duplicate: M, rejected_no_bytes: K, errors: [...]}`

The exporter script uploads bytes for every photo first (skipping already-present sha256s), then posts the JSONL. Order matters because the metadata import refuses rows whose bytes don't yet exist on Render — preventing dangling references.

**Retriever change:** when a query matches any of the V1 active class names (literal substring match against the caption), photo chunks are returned in retrieval results alongside text chunks. Standard BM25 ranking applies. No vector embedding for photos in V1.

**Citation format:** `[photo IMG-20230523-WA0009.jpg: no_hardhat (0.87), concrete_honeycomb (0.74)]` with a link to the photo (served via existing `/v1/files/{sha256}` endpoint — extended to serve the photo bytes, or a new `/v1/photos/{sha256}` if cleaner).

**Photo storage:** photos themselves live in a new `photos` table (defined in migration `0006`), keyed by `sha256` with `bytes BYTEA` (Postgres) / `BLOB` (SQLite). NOT object storage in V1 — keeps deployment simple; 208 photos × ~2 MB each = ~400 MB which fits the existing 1 GB disk plan. Phase 3 migrates to R2/S3 when corpus grows. Bytes are served back to the UI via `GET /v1/photos/{sha256}` (new endpoint, public-read with project-scope check against the `doc_index` row).

## Error Handling

- **Survey (Phase 0):** if Grounding DINO model/API unavailable, abort with a clear error pointing to install (`pip install groundingdino-py` or HuggingFace download instructions). Falls back to operator-only labelling if explicitly requested via `--no-prelabel`.
- **Training (Phase 1c):** validation gates that REFUSE to ship:
  - Any active class has fewer than 30 labelled examples → abort, list under-budget classes
  - Validation mAP@0.5 < 0.3 → ship the weights file but mark `model_grade: "experimental"` in metadata; export prevented unless `--force-low-quality` flag set
- **Inference (Phase 2a):** per-photo failures (corrupt image, PIL error, YOLO crash) are caught individually; failed photos exported with `inference_failed: true` + `inference_error: "ZeroDivisionError: ..."`. Pipeline does not abort on a single failure.
- **Export (Phase 2b):** retries with exponential backoff on 5xx; resumable (tracks `last_exported_sha256` in a state file); partial batches OK.

## Testing

| Test | Type | What it proves |
|---|---|---|
| `tests/test_safety_classes.py` | unit | Class registry parses; all IDs unique; active subset has weights_version set |
| `tests/test_label_format_roundtrip.py` | unit | Label Studio JSON → YOLO txt → re-import to LS keeps bboxes bit-identical |
| `tests/test_train_safety_qaqc_tinyset.py` | smoke | 5-photo, 1-class, 1-epoch training run produces a `.pt` file under 60s |
| `tests/test_infer_photo_metadata.py` | smoke | Run pipeline on 1 fixture image, assert output schema matches |
| `tests/test_admin_photo_import.py` | integration | POST JSONL → query `doc_index` → photo row present with `kind='photo'` |
| `tests/test_retriever_finds_photos.py` | integration | Insert photo with `concrete_crack` in caption → query "show me cracks" → photo in top-K |

All tests mock the live LLM and live Grounding DINO calls. Real model invocation is reserved for manual smoke tests on the operator's PC.

## What Goes On Local PC vs Render

| Artifact | PC | Render |
|---|---|---|
| `construction-3-001.zip` (raw) | yes | no |
| Extracted photos | yes (`data/training/raw_photos/`) | no |
| Label Studio + labels | yes | no |
| Trained `.pt` weights | yes (`data/models/`) | no in V1 (Phase 3 may copy) |
| `photo_metadata.jsonl` | yes (build artifact) | yes (imported via admin endpoint) |
| Photo bytes (served as RAG sources) | yes (originals) | yes (uploaded with metadata) |

## Open Items for Operator (after spec lock, during implementation)

- Confirm Render disk has room for ~400 MB of photos (current plan: 1 GB starter — should be fine)
- Decide whether the new `photos` table sits in the existing Postgres or a separate schema
- Confirm Label Studio is installable on the PC (free OSS, `pip install label-studio`) or prefer Roboflow / CVAT
- During Phase 0 survey, decide final V1 class list once detection counts are visible

## Phase Sequencing

| Phase | What ships | Operator action required |
|---|---|---|
| 0 | survey tool + class registry + Render migration | Run survey on PC, review counts, lock V1 classes |
| 1 | pre-label tool + training script + Label Studio workflow | Run pre-label on PC, correct in Label Studio, run training |
| 2 | inference script + export script + admin endpoint + retriever change | Run inference on PC, run export to Render, verify RAG retrieval |
| 3 (separate spec) | active-project upload pathway, optional Render-side runtime inference, active-learning loop, VLM caption pass | Out of scope for this design |

Each phase is independently shippable. Operator decides whether to gate phases behind manual review or auto-advance.
