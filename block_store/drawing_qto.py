"""Drawing QTO Block - Quantity Take-Off from DXF/DWG construction drawings"""

import os
import math
from typing import Any, Dict, List, Tuple
from app.core.universal_base import UniversalBlock


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
                {"name": "volumes", "type": "list", "unit": "m³", "label": "Volumes"},
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

        file_path = data.get("file_path") or params.get("file_path")
        if not file_path:
            return {"status": "error", "error": "No file_path provided"}
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

        msp = doc.modelspace()
        measurements = self._extract_measurements(msp, scale)
        areas = self._extract_areas(msp, scale, layer_filter, min_area)
        volumes = self._estimate_volumes(areas, params)
        layers = list({e.dxf.layer for e in msp if hasattr(e.dxf, "layer")})

        total_area = sum(a["area_m2"] for a in areas)
        total_length = sum(m["length_m"] for m in measurements)

        return {
            "status": "success",
            "measurements": measurements,
            "areas": areas,
            "volumes": volumes,
            "total_area_m2": round(total_area, 3),
            "total_length_m": round(total_length, 3),
            "entity_count": len(list(msp)),
            "layers": layers[:50],
            "drawing_units": str(doc.units),
        }

    def _extract_measurements(self, msp, scale: float) -> List[Dict]:
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
                    ) * scale / 1000  # mm → m
                    results.append({
                        "type": "line",
                        "length_m": round(length, 4),
                        "layer": entity.dxf.layer,
                        "start": [round(start.x * scale / 1000, 3), round(start.y * scale / 1000, 3)],
                        "end": [round(end.x * scale / 1000, 3), round(end.y * scale / 1000, 3)],
                    })
                elif etype == "CIRCLE":
                    radius = entity.dxf.radius * scale / 1000
                    circumference = 2 * math.pi * radius
                    results.append({
                        "type": "circle",
                        "radius_m": round(radius, 4),
                        "circumference_m": round(circumference, 4),
                        "length_m": round(circumference, 4),
                        "layer": entity.dxf.layer,
                    })
                elif etype == "ARC":
                    radius = entity.dxf.radius * scale / 1000
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
                    length = length * scale / 1000
                    results.append({
                        "type": "polyline",
                        "length_m": round(length, 4),
                        "closed": entity.is_closed,
                        "vertex_count": len(pts),
                        "layer": entity.dxf.layer,
                    })
                elif etype == "DIMENSION":
                    if hasattr(entity.dxf, "actual_measurement"):
                        val = entity.dxf.actual_measurement * scale / 1000
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
        self, msp, scale: float, layer_filter: List[str], min_area: float
    ) -> List[Dict]:
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
                    r = entity.dxf.radius * scale / 1000
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
                    coords = [(p[0] * scale / 1000, p[1] * scale / 1000) for p in pts]
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
                                    (v[0] * scale / 1000, v[1] * scale / 1000)
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
