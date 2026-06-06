"""Drawing QTO Block - Quantity Take-Off from DXF/DWG construction drawings"""

import os
import math
from typing import Any, Dict, List, Tuple
from app.core.universal_base import UniversalBlock


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
            return self._extract_from_pdf(file_path, params)

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


def _shoelace(coords: List[Tuple[float, float]]) -> float:
    n = len(coords)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += coords[i][0] * coords[j][1]
        area -= coords[j][0] * coords[i][1]
    return area / 2.0
