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
            {"icon": "📐", "label": "Full QTO", "prompt": "Extract all quantities from this drawing"},
            {"icon": "📏", "label": "Measurements", "prompt": "List all linear measurements"},
            {"icon": "🔲", "label": "Floor Areas", "prompt": "Calculate floor areas by room"},
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
        if ext == ".dwg":
            return {
                "status": "error",
                "error": (
                    "DWG format requires ODA File Converter. "
                    "Convert to DXF first: https://www.opendesign.com/guestfiles/oda_file_converter"
                ),
            }
        if ext != ".dxf":
            return {"status": "error", "error": f"Unsupported format: {ext}. Use .dxf"}

        try:
            import ezdxf
        except ImportError:
            return {"status": "error", "error": "ezdxf not installed. Run: pip install ezdxf"}

        try:
            doc = ezdxf.readfile(file_path)
        except Exception as e:
            return {"status": "error", "error": f"DXF read error: {e}"}

        scale = float(params.get("unit_scale", self.config.get("unit_scale", 1.0)))
        layer_filter = params.get("area_layer_filter", self.config.get("area_layer_filter", []))
        min_area = float(params.get("min_area_m2", self.config.get("min_area_m2", 0.01)))

        # Honour the DXF's declared units instead of assuming millimetres.
        to_metres_factor = _to_metres_factor(doc.units)
        unit_factor = scale * to_metres_factor

        msp = doc.modelspace()
        measurements = self._extract_measurements(msp, unit_factor)
        areas = self._extract_areas(msp, unit_factor, layer_filter, min_area)
        volumes = self._estimate_volumes(areas, params)
        layers = list({e.dxf.layer for e in msp if hasattr(e.dxf, "layer")})

        total_area = sum(a["area_m2"] for a in areas)
        total_length = sum(m["length_m"] for m in measurements)

        return {
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
        }

    def _extract_measurements(self, msp, unit_factor: float) -> List[Dict]:
        """``unit_factor`` converts raw drawing units straight to metres."""
        results = []
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
                elif etype in ("LWPOLYLINE", "POLYLINE"):
                    pts = list(entity.get_points() if etype == "LWPOLYLINE" else entity.points())
                    length = 0.0
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
                        "type": "polyline",
                        "length_m": round(length, 4),
                        "closed": entity.is_closed,
                        "vertex_count": len(pts),
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
        return results

    def _extract_areas(
        self, msp, unit_factor: float, layer_filter: List[str], min_area: float
    ) -> List[Dict]:
        """``unit_factor`` converts raw drawing units straight to metres."""
        results = []
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
                        for path in entity.paths:
                            if hasattr(path, "vertices") and len(path.vertices) >= 3:
                                coords = [
                                    (v[0] * unit_factor, v[1] * unit_factor)
                                    for v in path.vertices
                                ]
                                area = abs(_shoelace(coords))
                                if area >= min_area:
                                    results.append({
                                        "type": "hatch_area",
                                        "area_m2": round(area, 4),
                                        "layer": layer,
                                    })
            except Exception:
                continue
        return sorted(results, key=lambda x: x["area_m2"], reverse=True)

    def _estimate_volumes(self, areas: List[Dict], params: Dict) -> List[Dict]:
        height = float(params.get("height_m", 3.0))  # default floor height 3m
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
