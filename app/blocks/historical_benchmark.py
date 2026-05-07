"""Historical Benchmark Block — RS Means-style unit cost lookups and market ranges."""

from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock


class HistoricalBenchmarkBlock(UniversalBlock):
    name = "historical_benchmark"
    version = "1.0"
    description = "RS Means-style benchmark unit costs, cost ranges, and market data for construction items"
    layer = 2
    tags = ["construction", "cost", "benchmark", "rsmeans", "aec"]

    ui_schema = {
        "input": {
            "type": "object",
            "placeholder": "Pass item description + location for benchmark rates",
        },
        "params": {
            "fields": [
                {"name": "item", "type": "string", "label": "Item description"},
                {"name": "unit", "type": "string", "label": "Unit (m2, m3, kg, ea…)"},
                {"name": "location", "type": "string", "label": "Location / city"},
                {"name": "project_type", "type": "string", "label": "Project type"},
            ]
        },
    }

    # ------------------------------------------------------------------ #
    # Core rate database (USD, mid-2024 baseline)
    # ------------------------------------------------------------------ #
    _RATES: Dict[str, Dict] = {
        # Structural
        "concrete_c25_m3":       {"base": 130, "low": 100, "high": 175, "unit": "m3", "trade": "Structural"},
        "concrete_c30_m3":       {"base": 155, "low": 120, "high": 200, "unit": "m3", "trade": "Structural"},
        "concrete_c40_m3":       {"base": 180, "low": 145, "high": 235, "unit": "m3", "trade": "Structural"},
        "rebar_kg":              {"base": 1.90, "low": 1.40, "high": 2.60, "unit": "kg", "trade": "Structural"},
        "structural_steel_kg":   {"base": 3.50, "low": 2.80, "high": 4.80, "unit": "kg", "trade": "Structural"},
        "formwork_standard_m2":  {"base": 48, "low": 35, "high": 70, "unit": "m2", "trade": "Structural"},
        "formwork_soffit_m2":    {"base": 60, "low": 45, "high": 85, "unit": "m2", "trade": "Structural"},
        "piling_lm":             {"base": 290, "low": 200, "high": 420, "unit": "lm", "trade": "Groundworks"},
        "excavation_m3":         {"base": 24, "low": 14, "high": 40, "unit": "m3", "trade": "Groundworks"},
        # Masonry / Envelope
        "blockwork_m2":          {"base": 38, "low": 28, "high": 55, "unit": "m2", "trade": "Masonry"},
        "brickwork_m2":          {"base": 78, "low": 58, "high": 110, "unit": "m2", "trade": "Masonry"},
        "curtain_wall_m2":       {"base": 450, "low": 280, "high": 750, "unit": "m2", "trade": "Facades"},
        "glazing_standard_m2":   {"base": 190, "low": 130, "high": 290, "unit": "m2", "trade": "Facades"},
        "cladding_m2":           {"base": 220, "low": 140, "high": 380, "unit": "m2", "trade": "Facades"},
        "roofing_flat_m2":       {"base": 100, "low": 70, "high": 150, "unit": "m2", "trade": "Roofing"},
        "waterproofing_m2":      {"base": 42, "low": 28, "high": 65, "unit": "m2", "trade": "Waterproofing"},
        # Finishes
        "plaster_m2":            {"base": 30, "low": 20, "high": 45, "unit": "m2", "trade": "Finishes"},
        "drylining_m2":          {"base": 48, "low": 32, "high": 72, "unit": "m2", "trade": "Finishes"},
        "tiling_standard_m2":    {"base": 90, "low": 60, "high": 140, "unit": "m2", "trade": "Finishes"},
        "tiling_premium_m2":     {"base": 160, "low": 110, "high": 280, "unit": "m2", "trade": "Finishes"},
        "flooring_screed_m2":    {"base": 35, "low": 24, "high": 52, "unit": "m2", "trade": "Finishes"},
        "painting_m2":           {"base": 20, "low": 12, "high": 32, "unit": "m2", "trade": "Finishes"},
        "suspended_ceiling_m2":  {"base": 65, "low": 42, "high": 100, "unit": "m2", "trade": "Finishes"},
        "insulation_thermal_m2": {"base": 32, "low": 20, "high": 50, "unit": "m2", "trade": "Insulation"},
        # MEP
        "hvac_medium_m2":        {"base": 125, "low": 85, "high": 200, "unit": "m2", "trade": "Mechanical"},
        "electrical_standard_m2":{"base": 85, "low": 55, "high": 140, "unit": "m2", "trade": "Electrical"},
        "plumbing_standard_m2":  {"base": 68, "low": 45, "high": 110, "unit": "m2", "trade": "Plumbing"},
        "fire_protection_m2":    {"base": 38, "low": 25, "high": 62, "unit": "m2", "trade": "Fire Protection"},
        # Elements
        "door_internal_ea":      {"base": 900, "low": 600, "high": 1800, "unit": "ea", "trade": "Joinery"},
        "door_external_ea":      {"base": 2200, "low": 1400, "high": 4500, "unit": "ea", "trade": "Joinery"},
        "window_standard_ea":    {"base": 1300, "low": 800, "high": 2600, "unit": "ea", "trade": "Joinery"},
        "lift_passenger_ea":     {"base": 90000, "low": 60000, "high": 150000, "unit": "ea", "trade": "Vertical Transport"},
        "scaffold_m2":           {"base": 13, "low": 8, "high": 22, "unit": "m2", "trade": "Temporary Works"},
    }

    _LOCATION_FACTORS: Dict[str, float] = {
        "us national average": 1.00, "new york city": 1.35, "san francisco": 1.42,
        "los angeles": 1.28, "chicago": 1.18, "houston": 1.05,
        "dubai": 0.95, "abu dhabi": 0.92, "riyadh": 0.88, "jeddah": 0.90,
        "doha": 0.97, "kuwait city": 0.93,
        "london": 1.28, "manchester": 1.12, "paris": 1.22,
        "frankfurt": 1.18, "amsterdam": 1.20,
        "sydney": 1.15, "melbourne": 1.12,
        "singapore": 1.08, "hong kong": 1.25, "tokyo": 1.30,
        "toronto": 1.10, "mumbai": 0.45, "delhi": 0.42,
    }

    _PROJECT_FACTORS: Dict[str, float] = {
        "residential": 1.00, "commercial": 1.15, "industrial": 0.90,
        "hospital": 1.45, "education": 1.10, "hotel": 1.25,
        "general_building": 1.05, "infrastructure": 0.85, "mixed_use": 1.18,
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        action = params.get("action", data.get("action", "lookup"))

        if action == "lookup":
            return self._lookup(data, params)
        if action == "batch":
            return self._batch_lookup(data, params)
        if action == "location_factors":
            return {"status": "success", "location_factors": self._LOCATION_FACTORS}
        if action == "catalogue":
            return self._get_catalogue(params)

        return self._lookup(data, params)

    def _lookup(self, data: Dict, params: Dict) -> Dict:
        item = params.get("item") or data.get("item") or data.get("description") or data.get("text") or data.get("input", "")
        unit = (params.get("unit") or data.get("unit", "")).lower()
        location = (params.get("location") or data.get("location", "us national average")).lower()
        project_type = (params.get("project_type") or data.get("project_type", "general_building")).lower()

        loc_factor = self._get_location_factor(location)
        proj_factor = self._PROJECT_FACTORS.get(project_type, 1.05)

        rate_key, rate_data = self._find_best_match(item, unit)

        if not rate_data:
            return {
                "status": "not_found",
                "item": item,
                "message": f"No benchmark found for '{item}' ({unit}). Check catalogue for available items.",
            }

        base = rate_data["base"]
        adjusted = round(base * loc_factor * proj_factor, 2)

        return {
            "status": "success",
            "item": item,
            "matched_key": rate_key,
            "unit": rate_data["unit"],
            "trade": rate_data["trade"],
            "rates": {
                "base_usd": base,
                "low_usd": round(rate_data["low"] * loc_factor * proj_factor, 2),
                "high_usd": round(rate_data["high"] * loc_factor * proj_factor, 2),
                "adjusted_usd": adjusted,
            },
            "factors": {
                "location": location,
                "location_factor": loc_factor,
                "project_type": project_type,
                "project_factor": proj_factor,
            },
            "confidence": "high" if rate_key in item.lower().replace(" ", "_") else "medium",
            "source": "RSMeans-calibrated internal database (mid-2024 USD baseline)",
        }

    def _batch_lookup(self, data: Dict, params: Dict) -> Dict:
        items: List[Dict] = params.get("items") or data.get("items") or []
        location = (params.get("location") or data.get("location", "us national average")).lower()
        project_type = (params.get("project_type") or data.get("project_type", "general_building")).lower()

        results = []
        total = 0.0
        for item in items:
            result = self._lookup(
                {**data, **item},
                {"location": location, "project_type": project_type,
                 "item": item.get("item", item.get("description", "")),
                 "unit": item.get("unit", "")},
            )
            qty = float(item.get("quantity", 1))
            if result.get("status") == "success":
                line_total = round(result["rates"]["adjusted_usd"] * qty, 2)
                result["quantity"] = qty
                result["line_total"] = line_total
                total += line_total
            results.append(result)

        return {
            "status": "success",
            "action": "batch_lookup",
            "items_requested": len(items),
            "items_matched": len([r for r in results if r.get("status") == "success"]),
            "total_cost_usd": round(total, 2),
            "results": results,
        }

    def _get_catalogue(self, params: Dict) -> Dict:
        trade_filter = params.get("trade", "").lower()
        items = []
        for key, data in self._RATES.items():
            if trade_filter and trade_filter not in data["trade"].lower():
                continue
            items.append({
                "key": key,
                "unit": data["unit"],
                "trade": data["trade"],
                "base_rate_usd": data["base"],
                "range": f"{data['low']} – {data['high']}",
            })
        return {
            "status": "success",
            "action": "catalogue",
            "total_items": len(items),
            "items": items,
        }

    def _find_best_match(self, item: str, unit: str) -> tuple:
        n = item.lower()
        u = unit.lower()

        # Exact keyword matching in priority order
        if "curtain wall" in n or "curtain_wall" in n:
            return "curtain_wall_m2", self._RATES["curtain_wall_m2"]
        if "cladding" in n:
            return "cladding_m2", self._RATES["cladding_m2"]
        if "glazing" in n or "glass" in n:
            return "glazing_standard_m2", self._RATES["glazing_standard_m2"]
        if "lift" in n or "elevator" in n:
            return "lift_passenger_ea", self._RATES["lift_passenger_ea"]
        if "structural steel" in n or ("steel" in n and "kg" in u):
            return "structural_steel_kg", self._RATES["structural_steel_kg"]
        if "rebar" in n or "reinforcement" in n:
            return "rebar_kg", self._RATES["rebar_kg"]
        if "c40" in n or ("concrete" in n and "40" in n):
            return "concrete_c40_m3", self._RATES["concrete_c40_m3"]
        if "c30" in n or ("concrete" in n and "30" in n):
            return "concrete_c30_m3", self._RATES["concrete_c30_m3"]
        if "concrete" in n and "m3" in u:
            return "concrete_c25_m3", self._RATES["concrete_c25_m3"]
        if "soffit" in n or ("formwork" in n and "soffit" in n):
            return "formwork_soffit_m2", self._RATES["formwork_soffit_m2"]
        if "formwork" in n or "shuttering" in n:
            return "formwork_standard_m2", self._RATES["formwork_standard_m2"]
        if "pil" in n:
            return "piling_lm", self._RATES["piling_lm"]
        if "excavat" in n:
            return "excavation_m3", self._RATES["excavation_m3"]
        if "brick" in n:
            return "brickwork_m2", self._RATES["brickwork_m2"]
        if "block" in n and "m2" in u:
            return "blockwork_m2", self._RATES["blockwork_m2"]
        if "waterproof" in n or "membrane" in n:
            return "waterproofing_m2", self._RATES["waterproofing_m2"]
        if "roof" in n:
            return "roofing_flat_m2", self._RATES["roofing_flat_m2"]
        if "suspended ceiling" in n or "false ceiling" in n:
            return "suspended_ceiling_m2", self._RATES["suspended_ceiling_m2"]
        if "drylining" in n or "drywall" in n:
            return "drylining_m2", self._RATES["drylining_m2"]
        if "plaster" in n:
            return "plaster_m2", self._RATES["plaster_m2"]
        if "premium tile" in n or "marble" in n or "stone tile" in n:
            return "tiling_premium_m2", self._RATES["tiling_premium_m2"]
        if "tile" in n or "tiling" in n:
            return "tiling_standard_m2", self._RATES["tiling_standard_m2"]
        if "floor" in n and "screed" in n:
            return "flooring_screed_m2", self._RATES["flooring_screed_m2"]
        if "paint" in n:
            return "painting_m2", self._RATES["painting_m2"]
        if "insulation" in n:
            return "insulation_thermal_m2", self._RATES["insulation_thermal_m2"]
        if "hvac" in n or "mechanical" in n or "air" in n:
            return "hvac_medium_m2", self._RATES["hvac_medium_m2"]
        if "fire" in n and "protection" in n:
            return "fire_protection_m2", self._RATES["fire_protection_m2"]
        if "electrical" in n or "lighting" in n:
            return "electrical_standard_m2", self._RATES["electrical_standard_m2"]
        if "plumbing" in n or "sanitary" in n:
            return "plumbing_standard_m2", self._RATES["plumbing_standard_m2"]
        if "external door" in n:
            return "door_external_ea", self._RATES["door_external_ea"]
        if "door" in n:
            return "door_internal_ea", self._RATES["door_internal_ea"]
        if "window" in n:
            return "window_standard_ea", self._RATES["window_standard_ea"]
        if "scaffold" in n:
            return "scaffold_m2", self._RATES["scaffold_m2"]

        return "", None

    def _get_location_factor(self, location: str) -> float:
        loc = location.lower().strip()
        if loc in self._LOCATION_FACTORS:
            return self._LOCATION_FACTORS[loc]
        for key, factor in self._LOCATION_FACTORS.items():
            if key in loc or loc in key:
                return factor
        return 1.0
