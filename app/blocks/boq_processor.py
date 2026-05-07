"""BOQ Processor Block - Parse Excel/CSV Bills of Quantities into structured line items"""

import os
from typing import Any, Dict, List
from app.core.universal_base import UniversalBlock


class BOQProcessorBlock(UniversalBlock):
    name = "boq_processor"
    version = "1.0.0"
    description = "Parse Excel/CSV Bills of Quantities into structured quantities and cost breakdown"
    layer = 3
    tags = ["domain", "construction", "boq", "quantities", "excel"]
    requires = []

    default_config = {
        "currency": "USD",
        "include_zero_qty": False,
    }

    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".xlsx", ".xls", ".csv"],
            "placeholder": "Upload BOQ spreadsheet (.xlsx or .csv)...",
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "item_count", "type": "number", "label": "Line Items"},
                {"name": "total_cost", "type": "number", "unit": "USD", "label": "Total Cost"},
                {"name": "line_items", "type": "list", "label": "Line Items"},
                {"name": "cost_breakdown", "type": "json", "label": "Cost Breakdown"},
            ],
        },
        "quick_actions": [
            {"icon": "📊", "label": "Parse BOQ", "prompt": "Parse and summarize this Bill of Quantities"},
            {"icon": "💰", "label": "Cost Summary", "prompt": "Give me a cost breakdown by trade/division"},
        ],
    }

    # Common BOQ column name aliases
    _COL_MAP = {
        "description": ["description", "item_description", "work_item", "item", "activity", "desc", "name"],
        "quantity": ["quantity", "qty", "amount", "no", "number", "count"],
        "unit": ["unit", "uom", "u/m", "unit_of_measure", "measure"],
        "rate": ["rate", "unit_cost", "unit_price", "price", "unit_rate", "cost_per_unit", "cost/unit"],
        "total": ["total", "total_cost", "amount", "line_total", "extended_price", "cost", "value"],
        "section": ["section", "division", "trade", "category", "csi_div", "package", "work_package"],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        file_path = data.get("file_path") or params.get("file_path") or data.get("text") or data.get("input") or (input_data if isinstance(input_data, str) else "")
        if not file_path or not os.path.exists(str(file_path)):
            return self._demo_boq(params)

        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == ".csv":
                return await self._parse_csv(file_path, params)
            elif ext in (".xlsx", ".xls"):
                return await self._parse_excel(file_path, params)
            else:
                return {
                    "status": "error",
                    "error": f"Unsupported format: {ext}. Use .xlsx or .csv",
                }
        except ImportError as e:
            return {
                "status": "error",
                "error": f"Missing dependency: {e}. Run: pip install pandas openpyxl",
            }
        except Exception as e:
            return {"status": "error", "error": f"Parse error: {e}"}

    def _demo_boq(self, params: Dict) -> Dict:
        currency = params.get("currency", self.config.get("currency", "USD"))
        demo_items = [
            {"item_key": "substructure_excavation", "description": "Bulk Excavation to Formation Level", "quantity": 4500, "unit": "m3", "unit_cost": 18, "total_cost": 81000, "section": "Div 31 — Earthwork", "currency": currency},
            {"item_key": "concrete_raft_foundation", "description": "Reinforced Concrete Raft Foundation C30/37", "quantity": 380, "unit": "m3", "unit_cost": 420, "total_cost": 159600, "section": "Div 03 — Concrete", "currency": currency},
            {"item_key": "rebar_foundations", "description": "High-Yield Reinforcement Bar — Foundations", "quantity": 62, "unit": "tonne", "unit_cost": 1250, "total_cost": 77500, "section": "Div 03 — Concrete", "currency": currency},
            {"item_key": "structural_steel_frame", "description": "Structural Steelwork — Primary Frame", "quantity": 145, "unit": "tonne", "unit_cost": 2800, "total_cost": 406000, "section": "Div 05 — Metals", "currency": currency},
            {"item_key": "metal_deck_floor", "description": "Composite Metal Deck Floor Slab", "quantity": 2800, "unit": "m2", "unit_cost": 95, "total_cost": 266000, "section": "Div 05 — Metals", "currency": currency},
            {"item_key": "external_masonry", "description": "External Cavity Masonry Wall", "quantity": 1650, "unit": "m2", "unit_cost": 185, "total_cost": 305250, "section": "Div 04 — Masonry", "currency": currency},
            {"item_key": "roof_waterproofing", "description": "Single-Ply Membrane Roof Waterproofing", "quantity": 1100, "unit": "m2", "unit_cost": 125, "total_cost": 137500, "section": "Div 07 — Thermal & Moisture", "currency": currency},
            {"item_key": "curtain_walling", "description": "Aluminium Curtain Walling System", "quantity": 680, "unit": "m2", "unit_cost": 780, "total_cost": 530400, "section": "Div 08 — Openings", "currency": currency},
            {"item_key": "internal_partitions", "description": "Metal Stud Gypsum Board Partitions", "quantity": 3200, "unit": "m2", "unit_cost": 72, "total_cost": 230400, "section": "Div 09 — Finishes", "currency": currency},
            {"item_key": "raised_access_floor", "description": "Raised Access Floor System 600×600", "quantity": 2400, "unit": "m2", "unit_cost": 145, "total_cost": 348000, "section": "Div 09 — Finishes", "currency": currency},
            {"item_key": "hvac_ahu", "description": "Air Handling Units — Supply & Return", "quantity": 6, "unit": "nr", "unit_cost": 32000, "total_cost": 192000, "section": "Div 23 — HVAC", "currency": currency},
            {"item_key": "electrical_lv_panel", "description": "Low Voltage Distribution Panels", "quantity": 4, "unit": "nr", "unit_cost": 18500, "total_cost": 74000, "section": "Div 26 — Electrical", "currency": currency},
            {"item_key": "fire_suppression", "description": "Wet Pipe Sprinkler System", "quantity": 2800, "unit": "m2", "unit_cost": 38, "total_cost": 106400, "section": "Div 21 — Fire Suppression", "currency": currency},
            {"item_key": "external_works_paving", "description": "External Hard Landscaping & Paving", "quantity": 1800, "unit": "m2", "unit_cost": 95, "total_cost": 171000, "section": "Div 32 — Exterior Improvements", "currency": currency},
        ]
        total_cost = sum(i["total_cost"] for i in demo_items)
        section_totals: Dict = {}
        for item in demo_items:
            section_totals[item["section"]] = section_totals.get(item["section"], 0) + item["total_cost"]
        cost_breakdown = {
            s: {"total": round(v, 2), "percentage": round(v / total_cost * 100, 1)}
            for s, v in sorted(section_totals.items(), key=lambda x: x[1], reverse=True)
        }
        return {
            "status": "success",
            "demo_mode": True,
            "item_count": len(demo_items),
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "line_items": demo_items,
            "cost_breakdown": cost_breakdown,
            "sections": list(section_totals.keys()),
            "columns_detected": {"description": "description", "quantity": "quantity", "unit": "unit", "rate": "unit_cost", "total": "total_cost", "section": "section"},
        }

    async def _parse_csv(self, file_path: str, params: Dict) -> Dict:
        import pandas as pd
        df = pd.read_csv(file_path)
        return self._process_dataframe(df, params)

    async def _parse_excel(self, file_path: str, params: Dict) -> Dict:
        import pandas as pd
        sheet = params.get("sheet_name", 0)
        df = pd.read_excel(file_path, sheet_name=sheet, engine="openpyxl")
        return self._process_dataframe(df, params)

    def _resolve_columns(self, columns: List[str]) -> Dict[str, str]:
        """Map field names to actual DataFrame column names."""
        resolved = {}
        normalized = [c.strip().lower().replace(" ", "_") for c in columns]
        for field, candidates in self._COL_MAP.items():
            for c in candidates:
                if c in normalized:
                    resolved[field] = columns[normalized.index(c)]
                    break
        return resolved

    def _process_dataframe(self, df, params: Dict) -> Dict:
        df.columns = [str(c).strip() for c in df.columns]
        resolved = self._resolve_columns(list(df.columns))

        include_zero = params.get("include_zero_qty", self.config.get("include_zero_qty", False))
        currency = params.get("currency", self.config.get("currency", "USD"))

        line_items: List[Dict] = []
        section_totals: Dict[str, float] = {}

        for _, row in df.iterrows():
            description = str(row.get(resolved.get("description", ""), "")).strip()
            if not description or description.lower() == "nan":
                continue

            qty = _to_float(row.get(resolved.get("quantity", ""), 0))
            if not include_zero and qty == 0:
                continue

            rate = _to_float(row.get(resolved.get("rate", ""), 0))
            total = _to_float(row.get(resolved.get("total", ""), 0))
            if total == 0 and qty > 0 and rate > 0:
                total = qty * rate

            unit = str(row.get(resolved.get("unit", ""), "")).strip()
            section = str(row.get(resolved.get("section", ""), "General")).strip()
            if section.lower() == "nan":
                section = "General"

            item_key = description.lower().replace(" ", "_")[:50]

            line_items.append(
                {
                    "item_key": item_key,
                    "description": description,
                    "quantity": qty,
                    "unit": unit if unit != "nan" else "",
                    "unit_cost": rate,
                    "total_cost": round(total, 2),
                    "section": section,
                    "currency": currency,
                }
            )
            section_totals[section] = section_totals.get(section, 0.0) + total

        total_cost = sum(i["total_cost"] for i in line_items)
        cost_breakdown = {
            section: {
                "total": round(v, 2),
                "percentage": round(v / total_cost * 100, 1) if total_cost > 0 else 0,
            }
            for section, v in sorted(section_totals.items(), key=lambda x: x[1], reverse=True)
        }

        return {
            "status": "success",
            "item_count": len(line_items),
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "line_items": line_items,
            "cost_breakdown": cost_breakdown,
            "sections": list(section_totals.keys()),
            "columns_detected": resolved,
        }


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0
