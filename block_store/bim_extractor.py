"""BIM Extractor Block - Extract quantities, elements, and clash report from IFC BIM models"""

import os
import math
from typing import Any, Dict, List, Optional, Tuple
from app.core.universal_base import UniversalBlock


# IFC type → construction category mapping
IFC_CATEGORY_MAP: Dict[str, str] = {
    "IfcWall": "walls",
    "IfcWallStandardCase": "walls",
    "IfcSlab": "slabs",
    "IfcBeam": "beams",
    "IfcColumn": "columns",
    "IfcStair": "stairs",
    "IfcRoof": "roofs",
    "IfcDoor": "doors",
    "IfcWindow": "windows",
    "IfcCurtainWall": "curtain_walls",
    "IfcRailing": "railings",
    "IfcPile": "piles",
    "IfcFooting": "footings",
    "IfcMember": "members",
    "IfcPlate": "plates",
    "IfcPipeSegment": "pipes",
    "IfcDuctSegment": "ducts",
    "IfcCableSegment": "cables",
    "IfcFlowTerminal": "terminals",
    "IfcSpaceHeater": "hvac",
    "IfcLightFixture": "lighting",
    "IfcSpace": "spaces",
    "IfcZone": "zones",
    "IfcBuildingStorey": "storeys",
}


class BIMExtractorBlock(UniversalBlock):
    name = "bim_extractor"
    version = "1.0.0"
    description = "Extract building elements, quantities, and clash report from IFC BIM models"
    layer = 3
    tags = ["domain", "construction", "bim", "ifc", "quantities", "clash"]
    requires = []

    default_config = {
        "extract_properties": True,
        "run_clash_detection": True,
        "clash_tolerance_mm": 10.0,
        "max_elements": 10000,
    }

    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".ifc"],
            "placeholder": "Upload IFC BIM model...",
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "building_elements", "type": "list", "label": "Elements"},
                {"name": "quantities", "type": "json", "label": "Quantities"},
                {"name": "clash_report", "type": "json", "label": "Clashes"},
                {"name": "element_count", "type": "number", "label": "Total Elements"},
            ],
        },
        "quick_actions": [
            {"icon": "🏗️", "label": "Extract All", "prompt": "Extract all building elements and quantities"},
            {"icon": "⚡", "label": "Clash Detection", "prompt": "Run clash detection on this BIM model"},
            {"icon": "📊", "label": "Quantities", "prompt": "Extract material quantities for cost estimation"},
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
        if not file_path.lower().endswith(".ifc"):
            return {"status": "error", "error": "File must be an .ifc IFC model"}

        try:
            import ifcopenshell
            import ifcopenshell.util.element as ifc_util
        except ImportError:
            return {
                "status": "error",
                "error": "ifcopenshell not installed. Run: pip install ifcopenshell",
            }

        try:
            model = ifcopenshell.open(file_path)
        except Exception as e:
            return {"status": "error", "error": f"IFC open error: {e}"}

        max_el = int(params.get("max_elements", self.config.get("max_elements", 10000)))
        extract_props = params.get("extract_properties", self.config.get("extract_properties", True))
        run_clash = params.get("run_clash_detection", self.config.get("run_clash_detection", True))

        building_elements, quantities = self._extract_elements(
            model, ifc_util, max_el, extract_props
        )
        project_info = self._extract_project_info(model)
        storeys = self._extract_storeys(model)
        spaces = self._extract_spaces(model)
        clash_report = {}
        if run_clash:
            clash_report = self._basic_clash_report(model, building_elements)

        return {
            "status": "success",
            "building_elements": building_elements[:500],
            "quantities": quantities,
            "clash_report": clash_report,
            "project_info": project_info,
            "storeys": storeys,
            "spaces": spaces[:50],
            "element_count": len(building_elements),
            "ifc_schema": model.schema,
        }

    def _extract_elements(
        self, model, ifc_util, max_el: int, extract_props: bool
    ) -> Tuple[List[Dict], Dict]:
        elements: List[Dict] = []
        quantities: Dict[str, Any] = {
            cat: {"count": 0, "items": []}
            for cat in set(IFC_CATEGORY_MAP.values())
        }

        for ifc_type, category in IFC_CATEGORY_MAP.items():
            try:
                items = model.by_type(ifc_type)
            except Exception:
                continue
            for el in items:
                if len(elements) >= max_el:
                    break
                el_data = self._element_to_dict(el, category, ifc_util, extract_props)
                elements.append(el_data)
                quantities[category]["count"] += 1
                if len(quantities[category]["items"]) < 20:
                    quantities[category]["items"].append(el_data)

        quantities = {k: v for k, v in quantities.items() if v["count"] > 0}
        return elements, quantities

    def _element_to_dict(self, el, category: str, ifc_util, extract_props: bool) -> Dict:
        el_dict: Dict = {
            "id": el.GlobalId if hasattr(el, "GlobalId") else str(el.id()),
            "ifc_type": el.is_a(),
            "category": category,
            "name": getattr(el, "Name", "") or "",
            "description": getattr(el, "Description", "") or "",
            "object_type": getattr(el, "ObjectType", "") or "",
        }

        # Extract psets
        if extract_props:
            try:
                psets = ifc_util.get_psets(el)
                # Flatten to key/value — keep only scalar values
                flat_props: Dict[str, Any] = {}
                for pset_name, pset_vals in psets.items():
                    if isinstance(pset_vals, dict):
                        for prop_name, prop_val in pset_vals.items():
                            if isinstance(prop_val, (str, int, float, bool)):
                                flat_props[f"{pset_name}.{prop_name}"] = prop_val
                if flat_props:
                    el_dict["properties"] = flat_props

                # Pull common quantities
                for pset_name, pset_vals in psets.items():
                    if isinstance(pset_vals, dict):
                        for qname in ("NetVolume", "GrossVolume", "NetSideArea", "GrossSideArea",
                                      "NetFootprintArea", "GrossFootprintArea", "Length", "Width",
                                      "Height", "Depth", "Thickness", "NetArea", "GrossArea"):
                            if qname in pset_vals and isinstance(pset_vals[qname], (int, float)):
                                el_dict[qname.lower()] = pset_vals[qname]
            except Exception:
                pass

        # Material
        try:
            mats = ifc_util.get_materials(el)
            if mats:
                el_dict["materials"] = [
                    m.Name if hasattr(m, "Name") else str(m) for m in mats[:5]
                ]
        except Exception:
            pass

        return el_dict

    def _extract_project_info(self, model) -> Dict:
        try:
            projects = model.by_type("IfcProject")
            if projects:
                p = projects[0]
                return {
                    "name": getattr(p, "Name", "") or "",
                    "description": getattr(p, "Description", "") or "",
                    "global_id": p.GlobalId if hasattr(p, "GlobalId") else "",
                    "schema": model.schema,
                }
        except Exception:
            pass
        return {"schema": model.schema}

    def _extract_storeys(self, model) -> List[Dict]:
        try:
            storeys = model.by_type("IfcBuildingStorey")
            return [
                {
                    "name": getattr(s, "Name", "") or "",
                    "elevation": getattr(s, "Elevation", None),
                    "description": getattr(s, "Description", "") or "",
                }
                for s in storeys
            ]
        except Exception:
            return []

    def _extract_spaces(self, model) -> List[Dict]:
        try:
            spaces = model.by_type("IfcSpace")
            return [
                {
                    "name": getattr(s, "Name", "") or "",
                    "long_name": getattr(s, "LongName", "") or "",
                    "description": getattr(s, "Description", "") or "",
                }
                for s in spaces
            ]
        except Exception:
            return []

    def _basic_clash_report(self, model, elements: List[Dict]) -> Dict:
        """
        Basic clash detection: flag elements in same category with identical coordinates
        (placeholder for a full OBB/AABB spatial index approach).
        """
        clash_count = 0
        clashes: List[Dict] = []
        seen_positions: Dict[str, List[str]] = {}

        for el in elements:
            # Use name+type as a proxy position key (real detection needs geometry)
            key = f"{el.get('object_type', '')}:{el.get('name', '')}"
            if key:
                if key in seen_positions:
                    clash_count += 1
                    if len(clashes) < 20:
                        clashes.append({
                            "type": "name_duplicate",
                            "element_a": seen_positions[key][0],
                            "element_b": el["id"],
                            "category": el["category"],
                            "description": f"Duplicate {el['ifc_type']} name: {el.get('name')}",
                            "severity": "warning",
                        })
                else:
                    seen_positions[key] = [el["id"]]

        return {
            "clash_count": clash_count,
            "clashes": clashes,
            "detection_method": "name_duplicate_proxy",
            "note": (
                "Full geometric clash detection requires ifcopenshell.geom. "
                "Install ifcopenshell[all] for precise OBB intersection tests."
            ),
        }
