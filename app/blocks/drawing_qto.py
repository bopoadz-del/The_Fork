"""Drawing QTO Block - Quantity Take-Off from DXF/DWG construction drawings.

V1.1 adds a pdfplumber-based text-extraction path for PDF drawings that
classifies title-block fields, notes, dimensions, and cross-references
(per docs/superpowers/specs/2026-06-11-drawing-reader-design.md). The
existing DXF/PDF geometry extraction (fitz.get_drawings()) is preserved
so legacy callers keep their measurements/areas outputs; the new
structured drawing data goes under result["drawing"], and a
chunk-ready string lands at result["text"] for the RAG indexer.
"""

import os
import math
import re
from typing import Any, Dict, List, Tuple
from app.core.universal_base import UniversalBlock


# --- Discipline lookup ------------------------------------------------------
# DG2 (Diriyah Gate Phase II) drawing-number discipline codes. Module-level
# so tests can import + monkeypatch if a new project adds codes.
DISCIPLINE_FULL: Dict[str, str] = {
    "TM": "Traffic Management",
    "SW": "Storm Water",
    "SG": "Sewage",
    "EL": "Electrical",
    "LI": "Lighting",
    "ST": "Structural",
    "WS": "Water Supply",
    "IR": "Irrigation",
    "TL": "Telecom",
    "SE": "Security",
    "SF": "Safety",
    "IF": "Infrastructure",
}

# JCB-DWG drawing-number pattern observed across the DG2 corpus.
# Two token orders both appear in the wild:
#   IP-INF-053-0000-JCB-DWG-TM-200-1000005-A   (TM/SG/EL/TL sheets)
#   IP-INF-053-JCB-0000-DWG-WS-600-0000001-C   (WS sheets — tokens 4-5 swapped)
# Accept both by alternation. Shorter fallback covers project-specific
# schemes that don't use the full IP-INF prefix.
_DWG_NUMBER_FULL = re.compile(
    r"[A-Z]{2,}-[A-Z]{2,}-\d{3}-"
    r"(?:\d{4}-[A-Z]{3,}|[A-Z]{3,}-\d{4})-"
    r"[A-Z]{3,}-[A-Z]{2,}-\d{3}-\d{6,7}(?:-[A-Z0-9]+)?"
)
_DWG_NUMBER_SHORT = re.compile(r"[A-Z]{2,}-[A-Z]{2,}-\d{2,}-[A-Z0-9]+")

# Diriyah Gate area / district names that show up at large font sizes
# inside the main drawing region of regional / key-plan sheets and win the
# "largest cluster" title selection. They are sheet content, not
# drawing-title text. Match as whole-token uppercase.
_DG2_PLACE_NAMES = frozenset({
    "KHUZAMA", "AL TURAIF", "AL BUJAIRI", "AL QARYA", "AL QARYA AL KHADRA",
    "AL KHADRA", "AL SHOHDA", "AL DARIYAH", "DIRIYAH",
    "MECCA", "MADINAH", "RIYADH",
})


def _to_metres_factor(doc_units: int) -> float:
    """Map ezdxf unit codes (INSUNITS) to a multiplier that converts to metres.

    Reference ezdxf.units constants:
        0 = Unitless, 1 = Inches, 2 = Feet, 4 = Millimeters,
        5 = Centimeters, 6 = Meters
    Anything else falls back to 0.001 (assume mm), preserving prior behaviour.
    """
    mapping = {
        1: 0.0254,   # inches  -> m
        2: 0.3048,   # feet    -> m
        4: 0.001,    # mm      -> m
        5: 0.01,     # cm      -> m
        6: 1.0,      # m       -> m
    }
    try:
        return mapping.get(int(doc_units), 0.001)
    except (TypeError, ValueError):
        return 0.001


class DrawingQTOBlock(UniversalBlock):
    name = "drawing_qto"
    version = "1.0.0"
    description = "Extract measurements, areas, and volumes from DXF/DWG construction drawings"
    layer = 3
    tags = ["domain", "construction", "drawing", "qto", "dxf", "quantities"]
    requires = []

    default_config = {
        "unit_scale": 1.0,       # multiplier if drawing units ≠ mm
        "area_layer_filter": [],  # empty = all layers
        "min_area_m2": 0.01,
    }

    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".dxf", ".dwg"],
            "placeholder": "Upload DXF or DWG drawing...",
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "measurements", "type": "list", "label": "Linear Measurements"},
                {"name": "areas", "type": "list", "unit": "m²", "label": "Areas"},
                {"name": "estimated_volumes", "type": "list", "unit": "m³", "label": "Estimated Volumes (area × assumed height)"},
                {"name": "total_area_m2", "type": "number", "unit": "m²", "label": "Total Area"},
            ],
        },
        "quick_actions": [
            {"icon": "", "label": "Full QTO", "prompt": "Extract all quantities from this drawing"},
            {"icon": "", "label": "Measurements", "prompt": "List all linear measurements"},
            {"icon": "", "label": "Floor Areas", "prompt": "Calculate floor areas by room"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        # Support string path input directly, or InputAdapter {"text": "/path/to/file.dxf"}
        if isinstance(input_data, str) and not data:
            file_path = input_data
        else:
            file_path = data.get("file_path") or params.get("file_path") or data.get("text") or data.get("input") or ""
        if not file_path:
            return {"status": "error", "error": "No file_path provided. Requires a DXF or IFC file path."}
        if not os.path.exists(file_path):
            return {"status": "error", "error": f"File not found: {file_path}"}

        ext = os.path.splitext(file_path)[1].lower()

        # --- PDF input: extract vector drawings via PyMuPDF -----------------
        # PDF drawings carry their geometry as vector paths in the page
        # content stream. We can pull lines, rectangles, and closed shapes
        # straight out — coordinates come back in PDF points (1pt = 1/72")
        # at the page's scale, NOT real-world metres. The caller usually
        # knows the title-block scale (e.g. 1:100) and can pass
        # `pdf_scale_factor` to convert from page-units to metres.
        if ext == ".pdf":
            # Run the legacy geometry extractor for backward compat
            # (measurements, areas, estimated_volumes), then layer the new
            # text-based structured drawing fields on top.
            geom = self._extract_from_pdf(file_path, params)
            text_result = self._extract_drawing_text(file_path)
            # Merge: legacy keys first, new fields supplement.
            merged = dict(geom) if isinstance(geom, dict) else {}
            merged.update({
                "status": text_result.get("status", merged.get("status", "success")),
                "text": text_result.get("text", ""),
                "drawing": text_result.get("drawing", {}),
                "errors": text_result.get("errors", []),
            })
            return merged

        # --- DWG input: attempt ODA File Converter, else clear guidance ----
        if ext == ".dwg":
            converted = self._try_convert_dwg(file_path)
            if isinstance(converted, dict):  # error envelope
                return converted
            file_path = converted  # ezdxf will read the converted DXF below

        if ext not in (".dxf", ".dwg"):
            return {"status": "error", "error": f"Unsupported format: {ext}. Use .dxf, .dwg, or .pdf"}

        try:
            import ezdxf
        except ImportError:
            return {"status": "error", "error": "ezdxf not installed. Run: pip install ezdxf"}

        # open_plaintext transparently decrypts when DATA_ENCRYPTION_KEY is set
        # on the server (uploads go through file_crypto.write_document); no-op
        # for plaintext files.
        from app.core.file_crypto import open_plaintext
        try:
            with open_plaintext(file_path) as plain_path:
                doc = ezdxf.readfile(plain_path)
        except Exception as e:
            return {"status": "error", "error": f"DXF read error: {e}"}

        scale = float(params.get("unit_scale", self.config.get("unit_scale", 1.0)))
        layer_filter = params.get("area_layer_filter", self.config.get("area_layer_filter", []))
        min_area = float(params.get("min_area_m2", self.config.get("min_area_m2", 0.01)))

        # Honour the DXF's declared units instead of assuming millimetres.
        to_metres_factor = _to_metres_factor(doc.units)
        unit_factor = scale * to_metres_factor

        msp = doc.modelspace()
        measurements, bulge_segments_count = self._extract_measurements(msp, unit_factor)
        areas, hatch_hole_fallback = self._extract_areas(msp, unit_factor, layer_filter, min_area)
        volumes = self._estimate_volumes(areas, params)
        layers = list({e.dxf.layer for e in msp if hasattr(e.dxf, "layer")})

        total_area = sum(a["area_m2"] for a in areas)
        total_length = sum(m["length_m"] for m in measurements)

        response = {
            "status": "success",
            "measurements": measurements,
            "areas": areas,
            "estimated_volumes": volumes,
            "total_area_m2": round(total_area, 3),
            "total_length_m": round(total_length, 3),
            "entity_count": len(list(msp)),
            "layers": layers[:50],
            "drawing_units": str(doc.units),
            "input_units": doc.units,
            "to_metres_factor": to_metres_factor,
            "bulge_segments_count": bulge_segments_count,
            "polyline_area_note": (
                "Arc-bounded polygon area approximated as chord polygon area; "
                "difference < 5% for typical bulges"
            ),
        }
        if hatch_hole_fallback:
            response["hatch_hole_handling"] = "may include holes as positive area"
        return response

    def _extract_from_pdf(self, file_path: str, params: Dict) -> Dict:
        """Extract vector geometry from a PDF drawing via PyMuPDF.

        PDF drawings carry their geometry as ``page.get_drawings()`` items —
        each item has a ``type`` (``'l'`` line, ``'re'`` rect, ``'c'`` curve)
        and a ``rect`` bbox plus an ``items`` path. We translate those into
        the same shape ``drawing_qto`` produces for DXF (measurements +
        areas + estimated_volumes), with coordinates in **PDF points** by
        default. Pass ``pdf_scale_factor`` to convert to metres (e.g.
        ``0.000352778`` to go from 1pt to mm-of-paper, then multiply by the
        drawing's plot scale).
        """
        try:
            import fitz
        except ImportError:
            return {"status": "error", "error": "PyMuPDF (fitz) not installed."}
        from app.core.file_crypto import open_plaintext

        scale = float(params.get("pdf_scale_factor", 1.0))
        max_pages = int(params.get("max_pages", self.config.get("max_pages", 20)))
        min_length = float(params.get("min_length_units", 0.5))  # in input units

        measurements: List[Dict] = []
        areas: List[Dict] = []
        pages_inspected = 0
        page_dims: List[Dict] = []

        try:
            with open_plaintext(file_path) as plain_path:
                doc = fitz.open(plain_path)
                pages_inspected = min(len(doc), max_pages)
                for pi in range(pages_inspected):
                    page = doc[pi]
                    page_dims.append({
                        "page": pi + 1,
                        "width_pt": page.rect.width,
                        "height_pt": page.rect.height,
                    })
                    drawings = page.get_drawings() or []
                    for d in drawings:
                        # `items` is a list of path commands: ("l", p1, p2)
                        # for lines, ("re", rect) for rectangles, ("c", ...)
                        # for cubic Béziers, ("qu", quad) for quads.
                        for item in d.get("items") or []:
                            kind = item[0]
                            if kind == "l" and len(item) >= 3:
                                p1, p2 = item[1], item[2]
                                length_pt = math.hypot(p2.x - p1.x, p2.y - p1.y)
                                if length_pt < min_length:
                                    continue
                                measurements.append({
                                    "type": "line",
                                    "page": pi + 1,
                                    "length_pt": round(length_pt, 3),
                                    "length_scaled": round(length_pt * scale, 6),
                                    "start": [round(p1.x, 2), round(p1.y, 2)],
                                    "end":   [round(p2.x, 2), round(p2.y, 2)],
                                })
                            elif kind == "re" and len(item) >= 2:
                                r = item[1]
                                w, h = abs(r.width), abs(r.height)
                                if w < min_length and h < min_length:
                                    continue
                                area = w * h
                                areas.append({
                                    "type": "rect",
                                    "page": pi + 1,
                                    "width_pt": round(w, 3),
                                    "height_pt": round(h, 3),
                                    "area_pt2": round(area, 3),
                                    "area_scaled": round(area * scale * scale, 6),
                                })
        except Exception as e:
            return {"status": "error", "error": f"PDF drawing read error: {e}"}

        total_area = sum(a["area_pt2"] for a in areas)
        total_length = sum(m["length_pt"] for m in measurements)
        return {
            "status": "success",
            "source_format": "pdf",
            "pages_inspected": pages_inspected,
            "page_dimensions": page_dims,
            "measurements_count": len(measurements),
            "measurements": measurements[:200],   # cap for response size
            "areas_count": len(areas),
            "areas": areas[:200],
            "totals": {
                "length_pt": round(total_length, 3),
                "length_scaled": round(total_length * scale, 6),
                "area_pt2": round(total_area, 3),
                "area_scaled": round(total_area * scale * scale, 6),
            },
            "pdf_scale_factor": scale,
            "scale_note": (
                "PDF drawings carry no intrinsic scale — coordinates are in "
                "PDF points (1pt = 1/72\"). Pass `pdf_scale_factor` to convert "
                "to your target unit (e.g. for a 1:100 plotted drawing in mm, "
                "use 0.000352778 * 100 = 0.0352778 pt → m)."
            ),
        }

    # ====================================================================
    # V1.1 -- pdfplumber-based text extraction for CAD drawings
    # ====================================================================
    # See docs/superpowers/specs/2026-06-11-drawing-reader-design.md.
    # Coordinate orientation: pdfplumber returns y0 in PDF user-space
    # (0 = bottom of page, page.height = top). All "bottom 15%" zones
    # are y0 < page.height * 0.15. The right-20% fallback handles the
    # common DG2 landscape layout where the title block lives on the
    # right edge, not the bottom.

    def _extract_drawing_text(self, file_path: str) -> Dict:
        """Top-level text-extraction orchestrator: returns
        ``{"text", "drawing", "errors", "status"}``."""
        errors: List[str] = []
        try:
            import pdfplumber
        except ImportError:
            return {
                "status": "error",
                "text": "",
                "drawing": {},
                "errors": ["pdfplumber_not_installed"],
            }
        from app.core.file_crypto import open_plaintext

        page_full_raw_texts: List[str] = []
        try:
            with open_plaintext(file_path) as plain_path:
                try:
                    pdf = pdfplumber.open(plain_path)
                except Exception as exc:
                    msg = str(exc).lower()
                    if "password" in msg or "encrypt" in msg:
                        return {
                            "status": "error",
                            "text": "",
                            "drawing": {},
                            "errors": ["password_protected"],
                        }
                    return {
                        "status": "error",
                        "text": "",
                        "drawing": {},
                        "errors": [f"pdf_open_failed: {exc}"],
                    }

                with pdf:
                    if not pdf.pages:
                        return {
                            "status": "error",
                            "text": "",
                            "drawing": {},
                            "errors": ["no_pages"],
                        }

                    page_results = []
                    total_chars = 0
                    for page in pdf.pages:
                        chars = page.chars or []
                        total_chars += len(chars)
                        # Save raw full-page text for drawing-number fallback
                        # rescue (Bug 2: when title-block extractor returned a
                        # half-match like "IP-INF-053-JCB" we re-scan the full
                        # page for a proper JCB-DWG pattern).
                        page_full_raw_texts.append(
                            "".join(c["text"] for c in chars)
                        )
                        page_results.append(self._process_page(page, chars, errors))

                    if total_chars == 0:
                        # Scanned drawing / no text layer. OCR fallback is
                        # deferred to a follow-up task per the spec.
                        return {
                            "status": "error",
                            "text": "",
                            "drawing": {},
                            "errors": errors + ["no_text_layer_pdfplumber"],
                        }
        except Exception as exc:
            return {
                "status": "error",
                "text": "",
                "drawing": {},
                "errors": errors + [f"text_extract_failed: {exc}"],
            }

        # Multi-page combine. Take page-1 title block; if page N differs,
        # collapse-to-one-chunk for v1 and flag in errors.
        primary = page_results[0]
        for pr in page_results[1:]:
            if (pr["title_block"].get("drawing_number") and
                primary["title_block"].get("drawing_number") and
                pr["title_block"]["drawing_number"] !=
                    primary["title_block"]["drawing_number"]):
                errors.append("multi_drawing_pdf_collapsed_to_one_chunk")
                break

        # Aggregate notes/dimensions/cross_refs across pages
        all_notes: List[str] = []
        all_dims: List[str] = []
        all_refs: List[Dict] = []
        cad_filtered = 0
        # Dedup cross_refs across pages too, by (ref_type, target_drawing).
        seen_refs: set = set()
        for i, pr in enumerate(page_results, 1):
            if len(page_results) > 1:
                all_notes.extend(f"[Sheet {i}] {n}" for n in pr["notes"])
                all_dims.extend(f"[Sheet {i}] {d}" for d in pr["dimensions"])
            else:
                all_notes.extend(pr["notes"])
                all_dims.extend(pr["dimensions"])
            for r in pr["cross_refs"]:
                key = (r.get("ref_type"), r.get("target_drawing"))
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                all_refs.append(r)
            cad_filtered += pr["cad_tags_filtered_count"]

        # Guardrail cap: if dedup yielded >100 unique cross_refs we've almost
        # certainly regressed the regex; trim alphabetically and flag.
        if len(all_refs) > 100:
            errors.append("cross_refs_count_suspect_over_100")
            all_refs = sorted(
                all_refs,
                key=lambda r: (r.get("target_drawing") or "",
                               r.get("ref_type") or ""),
            )[:100]

        tb = dict(primary["title_block"])
        # Bug 2: reject drawing-number matches that aren't a full JCB
        # drawing-number on this corpus. The short fallback regex
        # sometimes grabs a half-match ("IP-INF-053-JCB") from a random
        # title-block fragment. Two valid full forms exist in the wild:
        #   ...-0000-JCB-DWG-...   (TM/SG/EL/TL token order)
        #   ...-JCB-0000-DWG-...   (WS token order, tokens 4-5 swapped)
        # Both contain BOTH "JCB" and "DWG" as separate tokens. If the
        # current value lacks either, re-scan the full page raw text.
        current_dn = tb.get("drawing_number")

        def _is_full_jcb(s: str) -> bool:
            u = s.upper()
            return "JCB" in u and "DWG" in u and u.count("-") >= 8
        if current_dn and not _is_full_jcb(current_dn):
            rescued = None
            for raw in page_full_raw_texts:
                m = _DWG_NUMBER_FULL.search(raw)
                if m and _is_full_jcb(m.group(0)):
                    rescued = m.group(0)
                    break
            if rescued:
                tb["drawing_number"] = rescued
                tb["discipline"] = None
                tb["discipline_full"] = None
                tb["revision"] = None
            else:
                # Drop the half-match so the filename fallback below fires.
                tb["drawing_number"] = None
                tb["discipline"] = None
                tb["discipline_full"] = None
                tb["revision"] = None
        if not tb.get("drawing_number"):
            tb["drawing_number"] = os.path.splitext(
                os.path.basename(file_path)
            )[0]
            errors.append("drawing_number_fallback_to_filename")
        # Re-derive discipline + revision from the (possibly rescued or
        # filename-fallback) drawing_number so all paths agree.
        if not tb.get("discipline") and tb.get("drawing_number"):
            tb["discipline"], tb["discipline_full"] = (
                self._discipline_from_number(tb["drawing_number"])
            )
        if not tb.get("revision") and tb.get("drawing_number"):
            tail = tb["drawing_number"].rsplit("-", 1)[-1]
            if 1 <= len(tail) <= 3 and re.fullmatch(r"[A-Z0-9]+", tail):
                # Don't accept obviously non-revision tails like "JCB" or
                # pure 6-7 digit sequence numbers.
                if tail not in ("JCB", "DWG") and not tail.isdigit():
                    tb["revision"] = tail
        # Phase 1.5 fallback: many JCB filenames carry the revision as a
        # trailing letter (e.g. ...-1000005-A.pdf). When title-block parse
        # and drawing-number-tail extraction both miss it, look at the
        # filename. Single uppercase letter immediately before the .pdf
        # extension wins. Numeric tails like "04" / "05" are NOT accepted
        # here because they are sheet-sequence indices, not revisions.
        if not tb.get("revision"):
            stem = os.path.splitext(os.path.basename(file_path))[0]
            m = re.search(r"-([A-Z])$", stem)
            if m:
                tb["revision"] = m.group(1)
                errors.append("revision_fallback_to_filename")

        # Bug 1: reject drawing_title that's actually the drawing_number with
        # a clustering artifact. The title-block extractor picks "longest
        # cluster" which often grabs the drawing number with a typo or trailing
        # revision letter glued on. Normalize both (uppercase + strip
        # non-alphanumerics) and reject any title that contains a 12+ char
        # substring of the normalized drawing_number.
        title = tb.get("drawing_title")
        dn = tb.get("drawing_number") or ""
        if title and dn:
            norm_title = re.sub(r"[^A-Z0-9]", "", title.upper())
            norm_dn = re.sub(r"[^A-Z0-9]", "", dn.upper())
            collision = False
            if norm_title and norm_dn:
                if norm_title == norm_dn:
                    collision = True
                elif len(norm_dn) >= 12:
                    for i in range(0, len(norm_dn) - 11):
                        if norm_dn[i:i + 12] in norm_title:
                            collision = True
                            break
            if collision:
                tb["drawing_title"] = None
                errors.append("drawing_title_not_found")

        drawing = {
            **tb,
            "notes": all_notes,
            "dimensions": all_dims,
            "cross_refs": all_refs,
            "cad_tags_filtered_count": cad_filtered,
            "n_pages": len(page_results),
        }
        raw_chunk = self._build_raw_chunk(drawing)
        return {
            "status": "success",
            "text": raw_chunk,
            "drawing": drawing,
            "errors": errors,
        }

    # --- per-page pipeline --------------------------------------------------
    def _process_page(self, page, chars: List[Dict], errors: List[str]) -> Dict:
        """Steps 1-5 of the spec for a single page."""
        title_block_chars, drawing_zone_chars = self._split_page_chars(
            page, chars
        )

        # --- Title-block fallback chain ------------------------------------
        # Use clustered line count as a "richness" signal -- below 5 lines
        # we fall back to the right-20% zone (landscape title blocks), then
        # to the full page.
        tb_lines = self._lines_from_chars(title_block_chars)
        if len(tb_lines) < 5:
            right_chars = [c for c in chars if c["x0"] >= page.width * 0.80]
            right_lines = self._lines_from_chars(right_chars)
            if len(right_lines) >= 5:
                title_block_chars = right_chars
                tb_lines = right_lines
            else:
                # full-page scan fallback
                title_block_chars = chars
                tb_lines = self._lines_from_chars(chars)
                errors.append("title_block_zone_fallback_full_page")

        title_block = self._extract_title_block(title_block_chars, page)

        # --- Drawing-zone classification -----------------------------------
        notes, dimensions, filtered_count = self._classify_drawing_zone(
            drawing_zone_chars or chars
        )

        # --- Cross-refs ----------------------------------------------------
        # Run on the *raw* char order of the page -- a single Tj operator's
        # chars are contiguous in page.chars even when the label is
        # rotated, which spatial reconstruction would scatter.
        raw_text = "".join(c["text"] for c in chars)
        cross_refs = self._extract_cross_refs(raw_text)

        return {
            "title_block": title_block,
            "notes": notes,
            "dimensions": dimensions,
            "cross_refs": cross_refs,
            "cad_tags_filtered_count": filtered_count,
        }

    # --- Step 1: page region split -----------------------------------------
    @staticmethod
    def _split_page_chars(page, chars: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Bottom 15% of page height -> title-block zone, rest -> drawing
        zone. pdfplumber y0=0 is the page bottom."""
        threshold = page.height * 0.15
        tb, dz = [], []
        for c in chars:
            if c["y0"] < threshold:
                tb.append(c)
            else:
                dz.append(c)
        return tb, dz

    # --- helpers: reconstruct lines from chars -----------------------------
    @staticmethod
    def _lines_from_chars(
        chars: List[Dict], y_tol: float = 2.0, x_gap: float = 30.0
    ) -> List[Dict]:
        """Cluster chars into reading-order lines.

        Returns a list of ``{"y": <y0>, "size": <avg>, "text": <str>}``
        records. Same line = chars within ``y_tol`` of the same y0
        baseline; same word/line continuation = adjacent chars within
        ``x_gap`` horizontally. This is intentionally lossy on rotated
        labels (those come out scrambled) -- spatial reconstruction is
        for the title block and dimension/notes blocks, not for
        rotated callouts (those go through raw-char-order cross-ref
        scanning instead).
        """
        if not chars:
            return []
        # Bucket by y0 rounded to tolerance
        buckets: Dict[float, List[Dict]] = {}
        for c in chars:
            key = round(c["y0"] / y_tol) * y_tol
            buckets.setdefault(key, []).append(c)

        lines: List[Dict] = []
        for y, cs in buckets.items():
            cs_sorted = sorted(cs, key=lambda c: c["x0"])
            # Split into runs separated by big x gaps
            run: List[Dict] = []
            last_x1 = None
            for c in cs_sorted:
                if last_x1 is not None and c["x0"] - last_x1 > x_gap:
                    if run:
                        lines.append(_line_from_run(y, run))
                    run = []
                run.append(c)
                last_x1 = c["x1"]
            if run:
                lines.append(_line_from_run(y, run))
        # Sort lines top-down for readability (PDF y0 large = top of page)
        lines.sort(key=lambda L: -L["y"])
        return lines

    # --- Step 2: title-block structured extraction -------------------------
    def _extract_title_block(self, tb_chars: List[Dict], page) -> Dict:
        """Extract drawing_number, title, discipline, revision, scale,
        date, drafter, checked_by, project_name, sheet_number from the
        title-block char set. Uses raw char order for the drawing
        number (a single rotated Tj operator can land contiguously in
        the content stream even when its bounding boxes scatter) and
        spatial clustering for everything else."""
        result: Dict[str, Any] = {
            "drawing_number": None,
            "drawing_title": None,
            "discipline": None,
            "discipline_full": None,
            "revision": None,
            "scale": None,
            "date": None,
            "drafter": None,
            "checked_by": None,
            "project_name": None,
            "sheet_number": None,
        }
        if not tb_chars:
            return result

        # --- Drawing number from raw char order ---------------------------
        raw = "".join(c["text"] for c in tb_chars)
        m = _DWG_NUMBER_FULL.search(raw)
        if not m:
            m = _DWG_NUMBER_SHORT.search(raw)
        if m:
            result["drawing_number"] = m.group(0)
            disc, disc_full = self._discipline_from_number(m.group(0))
            result["discipline"] = disc
            result["discipline_full"] = disc_full
            # Last hyphenated token of the JCB pattern is the revision
            tail = m.group(0).rsplit("-", 1)[-1]
            if 1 <= len(tail) <= 3 and re.fullmatch(r"[A-Z0-9]+", tail):
                result["revision"] = tail

        # --- Cluster title-block into lines for label-based fields --------
        lines = self._lines_from_chars(tb_chars, y_tol=2.0, x_gap=50.0)
        line_texts = [L["text"] for L in lines if L["text"].strip()]
        all_text = " \n".join(line_texts)

        # Scale: 1:NNN, NTS, N.T.S., NOT TO SCALE
        sm = re.search(
            r"(1\s*:\s*\d{1,5}|N\.?T\.?S\.?|NOT\s*TO\s*SCALE)",
            all_text,
            re.IGNORECASE,
        )
        if sm:
            result["scale"] = sm.group(1).strip()

        # Date: DD/MM/YY etc.
        dm = re.search(
            r"\b(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})\b", all_text
        )
        if dm:
            result["date"] = dm.group(1)

        # Sheet number: Sheet N of M, or N/M near "SHEET"
        shm = re.search(
            r"(?:SHEET|SH\.?)\s*[:\-]?\s*(\d+(?:\s*(?:OF|/)\s*\d+)?)",
            all_text,
            re.IGNORECASE,
        )
        if shm:
            result["sheet_number"] = shm.group(1).strip()

        # Project name: look for known DG2 project header keywords
        for L in lines:
            t = L["text"]
            if (re.search(r"DIRIYAH\s+GATE", t, re.IGNORECASE) or
                re.search(r"KING\s+KHALID", t, re.IGNORECASE) or
                re.search(r"INFRASTRUCTURE\s+DESIGN", t, re.IGNORECASE)):
                # Prefer the largest-font line
                if (result["project_name"] is None or
                    L["size"] > result.get("_project_size", 0)):
                    result["project_name"] = t.strip()
                    result["_project_size"] = L["size"]
        result.pop("_project_size", None)

        # Drafter: known DG2 drafter is "Jacobs"
        for L in lines:
            if re.search(r"\bJACOBS\b", L["text"], re.IGNORECASE):
                result["drafter"] = "Jacobs"
                break

        # Drawing title: largest text in the title block that isn't the
        # project name, dwg number, a cross-ref callout, or a known
        # boilerplate line.
        candidates = sorted(
            (L for L in lines if L["text"].strip()),
            key=lambda L: -L["size"],
        )
        for L in candidates:
            t = L["text"].strip()
            if not t or len(t) < 4:
                continue
            tu = t.upper()
            if result["drawing_number"] and result["drawing_number"] in tu:
                continue
            if result["project_name"] and t == result["project_name"]:
                continue
            # Bug 1.5b: reject cross-ref callouts as title candidates.
            # On TM detail sheets the longest cluster was the MATCH LINE
            # text. Skip anything that looks like a sheet-to-sheet ref.
            if re.search(
                r"\bMATCH\s*LINE\b|"
                r"\bCONT(?:INUED|D|\.)?\s*ON\b|"
                r"\bSEE\s+DWG\b|"
                r"\bREF(?:ER|\.)?[^\n]{0,40}?\b(?:SHEET|DWG|DRAWING)\b",
                tu,
            ):
                continue
            # Phase 1.6: reject pure-numeric and scale-shaped candidates.
            # On WS the title-block selection picked "1800" — a chainage
            # station number. The user wanted "1:1800" as scale, but
            # that lives in a different field; for drawing_title we just
            # refuse all numeric-shaped strings.
            t_compact = re.sub(r"\s+", "", t)
            if re.fullmatch(r"[\d.,/:\-]+", t_compact):
                continue
            # Reject scale labels (1:N or 1: N etc.) that escaped the
            # numeric check above due to embedded spaces.
            if re.fullmatch(r"1\s*:\s*\d+", t):
                continue
            # Phase 1.6: reject DG2 area / district names that win at
            # large font on regional key-plan sheets but are not
            # drawing-title text (KHUZAMA, AL TURAIF, etc.).
            tu_compact = re.sub(r"\s+", " ", tu).strip()
            if tu_compact in _DG2_PLACE_NAMES:
                continue
            if any(k in tu for k in (
                "DIRIYAH GATE", "KING KHALID", "INFRASTRUCTURE DESIGN",
                "KINGDOM OF SAUDI", "JACOBS", "WWW.", "P.O. BOX",
                "PRINCE SATTAM", "AL SHOHDA", "DATUM", "GEODETIC",
                "PROJECT SYSTEM", "ZONE:", "NOTES",
            )):
                continue
            result["drawing_title"] = t[:200]
            break

        return result

    @staticmethod
    def _discipline_from_number(dn: str) -> Tuple[str, str]:
        """Extract the 2-letter discipline code from a JCB-DWG drawing
        number and look up the human-readable name."""
        # JCB pattern places discipline 7th segment (e.g. ...-DWG-TM-200-...).
        # Fallback: any 2-letter segment that matches the table.
        parts = dn.split("-")
        for p in parts:
            if p in DISCIPLINE_FULL:
                return p, DISCIPLINE_FULL[p]
        return None, None

    # --- Step 3: drawing-zone font-size classification ---------------------
    def _classify_drawing_zone(
        self, dz_chars: List[Dict]
    ) -> Tuple[List[str], List[str], int]:
        """Classify drawing-zone text clusters by font size, then pattern-
        filter the kept text. Returns (notes, dimensions, filtered_count).
        """
        if not dz_chars:
            return [], [], 0

        # Build clusters: chars within 1px vertically + 5px horizontally are
        # one word; words on same y line within 30px gap are one line.
        lines = self._lines_from_chars(dz_chars, y_tol=1.5, x_gap=30.0)

        notes: List[str] = []
        dimensions: List[str] = []
        filtered = 0

        for L in lines:
            text = L["text"].strip()
            if not text:
                continue
            size = L["size"]

            # Size-based bucketing
            if size < 2.0:
                filtered += 1
                continue
            target_bucket = "notes" if size >= 4.0 else "dimensions"

            # Pattern filters apply to all kept clusters
            if _is_cad_tag(text):
                filtered += 1
                continue
            if _is_coordinate_pair(text):
                filtered += 1
                continue
            if len(text) <= 2:
                filtered += 1
                continue
            if _has_repeated_run(text, 4):
                filtered += 1
                continue

            if target_bucket == "notes":
                notes.append(text)
            else:
                dimensions.append(f"DIM: {text}")

        return notes, dimensions, filtered

    # --- Step 4: cross-ref extraction --------------------------------------
    # Sheet-identifier shape: either a JCB-style hyphenated number
    # (3+ tokens) OR a short sheet number (2-4 digits like "02", "10", "1234").
    # Loose `[A-Z0-9-]+` over-matched on SG (1755 hits) so we lock this down.
    _SHEET_ID_RE = re.compile(
        r"(?:[A-Z0-9]+(?:-[A-Z0-9]+){2,}|\d{2,4})"
    )

    @classmethod
    def _extract_cross_refs(cls, raw_text: str) -> List[Dict]:
        """Scan raw page text for match-line / continuation / reference
        callouts. Returns one dict per (ref_type, target_drawing) tuple
        after dedup. Caps at 100 entries per page (alphabetical) with a
        guardrail error if exceeded."""
        refs: List[Dict] = []
        # Tolerant patterns: allow arbitrary whitespace and optional colons
        # between tokens, and capture a strict sheet-id shape only.
        sheet = r"(?P<target>[A-Z0-9]+(?:-[A-Z0-9]+){2,}|\d{2,4})"
        patterns: List[Tuple[str, str]] = [
            ("match_line",
             r"MATCH\s*LINE\b[\s:.,\-]*"
             r"(?:FOR\s+REFERENCE\s+)?"
             r"(?:REFER(?:ENCE)?\s+(?:TO\s+)?)?"
             r"SHEET\s*(?:NO\.?)?\s*[:.\-]?\s*" + sheet),
            ("continuation",
             r"CONT(?:INUED|D|\.)?\.?\s*ON\s*[:.\-]?\s*" + sheet),
            ("reference",
             r"SEE\s+DWG\.?\s*[:.\-]?\s*" + sheet),
            ("reference",
             r"REF(?:ER|\.)?\.?\s*(?:TO\s+)?"
             r"(?:SHEET|DWG|DRAWING)\s+(?:NO\.?\s*)?[:.\-]?\s*" + sheet),
        ]
        # Dedup by (ref_type, target_drawing) — repeated identical match-line
        # callouts collapse to one entry.
        dedup: Dict[Tuple[str, str], Dict] = {}
        for ref_type, pat in patterns:
            for m in re.finditer(pat, raw_text, re.IGNORECASE | re.MULTILINE):
                target = (m.group("target") or "").strip().upper()
                if not target or len(target) < 2:
                    continue
                key = (ref_type, target)
                if key in dedup:
                    continue
                dedup[key] = {
                    "ref_type": ref_type,
                    "target_drawing": target,
                    "raw": m.group(0).strip(),
                }
        refs = list(dedup.values())
        return refs

    # --- Step 6: raw chunk builder -----------------------------------------
    @staticmethod
    def _build_raw_chunk(drawing: Dict) -> str:
        """Assemble the RAG-indexable chunk per the spec's template."""
        lines: List[str] = []
        header = drawing.get("drawing_number") or "(unknown)"
        title = drawing.get("drawing_title")
        disc_full = drawing.get("discipline_full") or drawing.get("discipline") or ""
        rev = drawing.get("revision") or ""
        head_bits = [header]
        if title:
            head_bits.append(f"-- {title}")
        meta = []
        if disc_full:
            meta.append(disc_full)
        if rev:
            meta.append(f"Rev {rev}")
        if meta:
            head_bits.append(f"({', '.join(meta)})")
        lines.append(" ".join(head_bits))

        meta_line_bits = []
        if drawing.get("scale"):
            meta_line_bits.append(f"Scale: {drawing['scale']}")
        if drawing.get("date"):
            meta_line_bits.append(f"Date: {drawing['date']}")
        if drawing.get("project_name"):
            meta_line_bits.append(f"Project: {drawing['project_name']}")
        if drawing.get("sheet_number"):
            meta_line_bits.append(f"Sheet: {drawing['sheet_number']}")
        if meta_line_bits:
            lines.append(" | ".join(meta_line_bits))

        notes = drawing.get("notes") or []
        if notes:
            lines.append("")
            lines.append("Notes:")
            for n in notes:
                lines.append(f"- {n}")

        refs = drawing.get("cross_refs") or []
        if refs:
            lines.append("")
            lines.append("References:")
            for r in refs:
                lines.append(
                    f"- {r['ref_type']}: {r['target_drawing']} ({r['raw']})"
                )
        return "\n".join(lines)

    # ====================================================================

    def _try_convert_dwg(self, file_path: str):
        """Best-effort DWG → DXF conversion via ODA File Converter CLI.

        Returns the converted DXF path on success, or a structured error
        dict on failure. The CLI is shipped as ``ODAFileConverter`` on most
        Linux/Mac installs and as ``ODAFileConverter.exe`` on Windows; we
        also look for ``oda_file_converter`` for image builds that ship a
        symlink. If neither is present, return the long-standing
        "convert to DXF first" guidance.
        """
        import shutil
        import subprocess
        import tempfile

        for candidate in ("ODAFileConverter", "ODAFileConverter.exe",
                          "oda_file_converter", "oda-file-converter"):
            tool = shutil.which(candidate)
            if tool:
                break
        else:
            return {
                "status": "error",
                "error": (
                    "DWG format requires the ODA File Converter CLI, which is "
                    "not bundled in this image (no pure-Python DWG reader "
                    "exists). Either: (a) install ODA File Converter — "
                    "https://www.opendesign.com/guestfiles/oda_file_converter — "
                    "and ensure `ODAFileConverter` is on PATH, or (b) export "
                    "the drawing as .dxf from AutoCAD/BricsCAD/LibreCAD "
                    "(File → Save As → DXF R2018) and upload that."
                ),
                "hint": "Upload the .dxf instead of .dwg",
            }

        try:
            with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
                # ODA CLI converts an input DIRECTORY to an output directory.
                # Copy the single DWG into a clean source dir, then convert.
                src_path = os.path.join(src_dir, os.path.basename(file_path))
                from app.core.file_crypto import open_plaintext
                with open_plaintext(file_path) as plain:
                    with open(plain, "rb") as fh, open(src_path, "wb") as fo:
                        fo.write(fh.read())
                # Args: in_dir out_dir output_version output_format
                #       (ACAD2018, DXF, recurse-flag, audit-flag)
                subprocess.run(
                    [tool, src_dir, dst_dir, "ACAD2018", "DXF", "0", "1"],
                    timeout=60, check=False, capture_output=True,
                )
                dxf_name = os.path.splitext(os.path.basename(file_path))[0] + ".dxf"
                converted = os.path.join(dst_dir, dxf_name)
                if not os.path.exists(converted):
                    return {
                        "status": "error",
                        "error": (
                            "ODA File Converter ran but produced no DXF; the "
                            "DWG may be corrupt or a future-version export "
                            "the bundled CLI doesn't yet support."
                        ),
                    }
                # Move into a path that survives the temp-dir teardown.
                stable = os.path.join(
                    tempfile.gettempdir(), f"converted_{os.path.basename(dxf_name)}"
                )
                with open(converted, "rb") as fh, open(stable, "wb") as fo:
                    fo.write(fh.read())
                return stable
        except FileNotFoundError as e:
            return {"status": "error", "error": f"DWG conversion launcher error: {e}"}
        except Exception as e:
            return {"status": "error", "error": f"DWG conversion failed: {e}"}

    def _extract_measurements(self, msp, unit_factor: float) -> Tuple[List[Dict], int]:
        """``unit_factor`` converts raw drawing units straight to metres.

        Returns (measurements, bulge_segments_count) so the caller can know
        how many LWPOLYLINE segments were arc-faced vs straight chords.
        """
        results = []
        bulge_segments_count = 0
        # Import bulge_to_arc lazily; only LWPOLYLINE with non-zero bulge needs it.
        try:
            from ezdxf.math import bulge_to_arc
        except Exception:
            bulge_to_arc = None
        for entity in msp:
            etype = entity.dxftype()
            try:
                if etype == "LINE":
                    start = entity.dxf.start
                    end = entity.dxf.end
                    length = math.dist(
                        (start.x, start.y, start.z),
                        (end.x, end.y, end.z)
                    ) * unit_factor
                    results.append({
                        "type": "line",
                        "length_m": round(length, 4),
                        "layer": entity.dxf.layer,
                        "start": [round(start.x * unit_factor, 3), round(start.y * unit_factor, 3)],
                        "end": [round(end.x * unit_factor, 3), round(end.y * unit_factor, 3)],
                    })
                elif etype == "CIRCLE":
                    radius = entity.dxf.radius * unit_factor
                    circumference = 2 * math.pi * radius
                    results.append({
                        "type": "circle",
                        "radius_m": round(radius, 4),
                        "circumference_m": round(circumference, 4),
                        "length_m": round(circumference, 4),
                        "layer": entity.dxf.layer,
                    })
                elif etype == "ARC":
                    radius = entity.dxf.radius * unit_factor
                    start_angle = math.radians(entity.dxf.start_angle)
                    end_angle = math.radians(entity.dxf.end_angle)
                    if end_angle < start_angle:
                        end_angle += 2 * math.pi
                    arc_length = radius * (end_angle - start_angle)
                    results.append({
                        "type": "arc",
                        "radius_m": round(radius, 4),
                        "arc_length_m": round(arc_length, 4),
                        "length_m": round(arc_length, 4),
                        "layer": entity.dxf.layer,
                    })
                elif etype == "LWPOLYLINE":
                    # get_points() returns (x, y, start_w, end_w, bulge) tuples.
                    pts = list(entity.get_points())
                    length = 0.0
                    entity_bulge_segs = 0

                    def seg_len(p_a, p_b):
                        # p_a is the start vertex (carries the bulge to p_b).
                        nonlocal entity_bulge_segs
                        bulge = p_a[4] if len(p_a) >= 5 else 0.0
                        if bulge_to_arc is not None and abs(bulge) >= 1e-9:
                            try:
                                _center, _start_a, _end_a, radius = bulge_to_arc(
                                    (p_a[0], p_a[1]), (p_b[0], p_b[1]), bulge
                                )
                                entity_bulge_segs += 1
                                return radius * abs(_end_a - _start_a)
                            except Exception:
                                pass
                        return math.dist((p_a[0], p_a[1]), (p_b[0], p_b[1]))

                    for i in range(len(pts) - 1):
                        length += seg_len(pts[i], pts[i + 1])
                    if entity.is_closed and len(pts) > 1:
                        length += seg_len(pts[-1], pts[0])
                    length = length * unit_factor
                    bulge_segments_count += entity_bulge_segs
                    results.append({
                        "type": "polyline",
                        "length_m": round(length, 4),
                        "closed": entity.is_closed,
                        "vertex_count": len(pts),
                        "bulge_segments": entity_bulge_segs,
                        "layer": entity.dxf.layer,
                    })
                elif etype == "POLYLINE":
                    pts = list(entity.points())
                    # POLYLINE flag bit 8 = is_3d_polyline (also exposed as
                    # `is_3d_polyline` attribute on ezdxf objects).
                    is_3d = bool(getattr(entity, "is_3d_polyline", False))
                    if not is_3d:
                        try:
                            is_3d = bool(int(getattr(entity.dxf, "flags", 0)) & 8)
                        except Exception:
                            is_3d = False

                    def _pt_xyz(p):
                        # Vertex coords may be Vec3 or tuple — be defensive.
                        x = getattr(p, "x", None)
                        if x is None:
                            x = p[0]
                            y = p[1]
                            z = p[2] if len(p) > 2 else 0.0
                        else:
                            y = p.y
                            z = getattr(p, "z", 0.0)
                        return (x, y, z)

                    length = 0.0
                    if is_3d:
                        coords = [_pt_xyz(p) for p in pts]
                        for i in range(len(coords) - 1):
                            length += math.dist(coords[i], coords[i + 1])
                        if entity.is_closed and len(coords) > 1:
                            length += math.dist(coords[-1], coords[0])
                    else:
                        for i in range(len(pts) - 1):
                            length += math.dist(
                                (pts[i][0], pts[i][1]),
                                (pts[i + 1][0], pts[i + 1][1])
                            )
                        if entity.is_closed and len(pts) > 1:
                            length += math.dist(
                                (pts[-1][0], pts[-1][1]),
                                (pts[0][0], pts[0][1])
                            )
                    length = length * unit_factor
                    results.append({
                        "type": "polyline_3d" if is_3d else "polyline",
                        "length_m": round(length, 4),
                        "closed": entity.is_closed,
                        "vertex_count": len(pts),
                        "is_3d": is_3d,
                        "layer": entity.dxf.layer,
                    })
                elif etype == "DIMENSION":
                    if hasattr(entity.dxf, "actual_measurement"):
                        val = entity.dxf.actual_measurement * unit_factor
                        results.append({
                            "type": "dimension",
                            "length_m": round(val, 4),
                            "layer": entity.dxf.layer,
                            "text": getattr(entity.dxf, "text", ""),
                        })
            except Exception:
                continue
        return results, bulge_segments_count

    def _extract_areas(
        self, msp, unit_factor: float, layer_filter: List[str], min_area: float
    ) -> Tuple[List[Dict], bool]:
        """``unit_factor`` converts raw drawing units straight to metres.

        Returns (areas, hatch_hole_fallback). hatch_hole_fallback is True if
        any HATCH path lacked readable path_type_flags so the caller is
        warned that holes may have been added as positive area.
        """
        results = []
        hatch_hole_fallback = False
        try:
            from shapely.geometry import Polygon
            use_shapely = True
        except ImportError:
            use_shapely = False

        for entity in msp:
            etype = entity.dxftype()
            layer = getattr(entity.dxf, "layer", "0")
            if layer_filter and layer not in layer_filter:
                continue
            try:
                if etype == "CIRCLE":
                    r = entity.dxf.radius * unit_factor
                    area = math.pi * r * r
                    if area >= min_area:
                        results.append({
                            "type": "circle",
                            "area_m2": round(area, 4),
                            "perimeter_m": round(2 * math.pi * r, 4),
                            "layer": layer,
                        })
                elif etype in ("LWPOLYLINE", "POLYLINE") and entity.is_closed:
                    pts = list(entity.get_points() if etype == "LWPOLYLINE" else entity.points())
                    coords = [(p[0] * unit_factor, p[1] * unit_factor) for p in pts]
                    if use_shapely and len(coords) >= 3:
                        poly = Polygon(coords)
                        area = poly.area
                        perim = poly.length
                    else:
                        area = abs(_shoelace(coords))
                        perim = sum(
                            math.dist(coords[i], coords[(i + 1) % len(coords)])
                            for i in range(len(coords))
                        )
                    if area >= min_area:
                        results.append({
                            "type": "polyline_area",
                            "area_m2": round(area, 4),
                            "perimeter_m": round(perim, 4),
                            "vertex_count": len(pts),
                            "layer": layer,
                        })
                elif etype == "HATCH":
                    if hasattr(entity, "paths"):
                        # Aggregate one entry per HATCH entity: external/outermost
                        # boundary paths add area, internal islands subtract it.
                        # If we can't read path_type_flags, fall back to summing
                        # |shoelace| per path (legacy behaviour) and flag it.
                        net_area = 0.0
                        per_path_legacy_area = 0.0
                        flags_readable = True
                        path_count = 0
                        for path in entity.paths:
                            if not (hasattr(path, "vertices") and len(path.vertices) >= 3):
                                continue
                            path_count += 1
                            coords = [
                                (v[0] * unit_factor, v[1] * unit_factor)
                                for v in path.vertices
                            ]
                            a = abs(_shoelace(coords))
                            per_path_legacy_area += a
                            ptf = getattr(path, "path_type_flags", None)
                            if ptf is None:
                                flags_readable = False
                                continue
                            try:
                                ptf_int = int(ptf)
                            except Exception:
                                flags_readable = False
                                continue
                            # Bit 1 = external boundary, Bit 4 = outermost.
                            # Either marks an outer (additive) contour; otherwise
                            # treat as a hole/island to subtract.
                            if ptf_int & 1 or ptf_int & 4:
                                net_area += a
                            else:
                                net_area -= a
                        if path_count == 0:
                            continue
                        if flags_readable:
                            area_value = max(net_area, 0.0)
                        else:
                            hatch_hole_fallback = True
                            area_value = per_path_legacy_area
                        if area_value >= min_area:
                            results.append({
                                "type": "hatch_area",
                                "area_m2": round(area_value, 4),
                                "path_count": path_count,
                                "hole_handling": (
                                    "outer_minus_holes" if flags_readable
                                    else "may_include_holes_as_positive_area"
                                ),
                                "layer": layer,
                            })
            except Exception:
                continue
        return sorted(results, key=lambda x: x["area_m2"], reverse=True), hatch_hole_fallback

    def _estimate_volumes(self, areas: List[Dict], params: Dict) -> List[Dict]:
        # Default ceiling height comes from app.core.construction_constants
        # so all blocks share the same domain assumption. Caller overrides
        # via params["height_m"] for project-specific data.
        from app.core.construction_constants import DEFAULT_CEILING_HEIGHT_M
        height = float(params.get("height_m", DEFAULT_CEILING_HEIGHT_M))
        volumes = []
        for a in areas:
            if a["area_m2"] > 1.0:
                volumes.append({
                    "type": f"{a['type']}_volume",
                    "area_m2": a["area_m2"],
                    "height_m": height,
                    "assumed_height_m": height,
                    "method": "area_x_height_assumption",
                    "volume_m3": round(a["area_m2"] * height, 4),
                    "layer": a.get("layer", ""),
                })
        return volumes


def _line_from_run(y: float, run: List[Dict]) -> Dict:
    """Build a line record from a list of chars that share a y baseline."""
    text = "".join(c["text"] for c in run)
    sizes = [c["size"] for c in run if c.get("size")]
    avg_size = sum(sizes) / len(sizes) if sizes else 0.0
    return {"y": y, "size": avg_size, "text": text}


# Pure CAD-tag patterns (all-caps + digits + hyphens, 4-15 chars, no spaces)
_CAD_TAG_RE = re.compile(r"^[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8}){1,4}$")
_COORD_PAIR_RE = re.compile(r"^\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*$")


def _is_cad_tag(text: str) -> bool:
    t = text.strip()
    if not t or " " in t:
        return False
    if not (4 <= len(t) <= 15):
        return False
    # All-caps + digits + hyphens, must have at least one digit AND a hyphen
    if "-" not in t or not any(ch.isdigit() for ch in t):
        return False
    return bool(_CAD_TAG_RE.match(t))


def _is_coordinate_pair(text: str) -> bool:
    return bool(_COORD_PAIR_RE.match(text.strip()))


def _has_repeated_run(text: str, n: int) -> bool:
    """True if any single token repeats >= n times consecutively."""
    tokens = text.split()
    if len(tokens) < n:
        return False
    run = 1
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            run += 1
            if run >= n:
                return True
        else:
            run = 1
    return False


def _shoelace(coords: List[Tuple[float, float]]) -> float:
    n = len(coords)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += coords[i][0] * coords[j][1]
        area -= coords[j][0] * coords[i][1]
    return area / 2.0
