"""Historical benchmark block — lightweight, extensible unit-rate source.

Provides plausible, clearly-labeled construction unit rates for common items
(concrete, steel, formwork, etc.) with regional location factors. The block
is intentionally simple: no drifting hardcoded RS-Means snapshot, no ML model.
Rates can be overridden per-item via the learning_engine / record action so
the platform accumulates real project data over time.
"""

from typing import Any, Dict, Optional

from app.core.universal_base import UniversalBlock


# Base USD rates for frequently-estimated construction items.
# These are deliberately conservative US-national/industry-average figures;
# the response flags them as estimates and recommends supplier quotes.
_BASE_RATES: Dict[str, Dict[str, Any]] = {
    "concrete": {
        "unit": "m3",
        "base_usd": 150.0,
        "note": "Ready-mix concrete, typical 30 MPa.",
    },
    "ready-mix concrete": {
        "unit": "m3",
        "base_usd": 150.0,
        "note": "Ready-mix concrete, typical 30 MPa.",
    },
    "steel": {
        "unit": "kg",
        "base_usd": 1.0,
        "note": "Reinforcing steel / rebar.",
    },
    "rebar": {
        "unit": "kg",
        "base_usd": 1.0,
        "note": "Reinforcing steel / rebar.",
    },
    "reinforcing steel": {
        "unit": "kg",
        "base_usd": 1.0,
        "note": "Reinforcing steel / rebar.",
    },
    "formwork": {
        "unit": "m2",
        "base_usd": 35.0,
        "note": "Traditional formwork, including labor for erection/stripping.",
    },
    "structural steel": {
        "unit": "kg",
        "base_usd": 2.5,
        "note": "Fabricated structural steel sections.",
    },
    "blockwork": {
        "unit": "m2",
        "base_usd": 45.0,
        "note": "Concrete masonry unit blockwork.",
    },
    "plaster": {
        "unit": "m2",
        "base_usd": 18.0,
        "note": "Internal plaster finish.",
    },
    "paint": {
        "unit": "m2",
        "base_usd": 12.0,
        "note": "Emulsion paint, two coats.",
    },
    "tiles": {
        "unit": "m2",
        "base_usd": 60.0,
        "note": "Ceramic floor/wall tiles including fixing.",
    },
    "excavation": {
        "unit": "m3",
        "base_usd": 15.0,
        "note": "Bulk excavation, machine.",
    },
    "backfill": {
        "unit": "m3",
        "base_usd": 20.0,
        "note": "Imported granular backfill and compaction.",
    },
    "asphalt": {
        "unit": "m2",
        "base_usd": 40.0,
        "note": "Asphalt paving, 50 mm.",
    },
    "earthworks": {
        "unit": "m3",
        "base_usd": 15.0,
        "note": "Bulk earthworks.",
    },
}

# Regional location factors applied to base rates.
_LOCATION_FACTORS: Dict[str, float] = {
    "us national average": 1.0,
    "united states": 1.0,
    "usa": 1.0,
    "saudi arabia": 0.85,
    "riyadh": 0.85,
    "jeddah": 0.88,
    "uae": 0.95,
    "dubai": 0.95,
    "abudhabi": 0.95,
    "abu dhabi": 0.95,
    "qatar": 0.98,
    "doha": 0.98,
    "kuwait": 0.92,
    "bahrain": 0.90,
    "egypt": 0.55,
    "cairo": 0.55,
    "uk": 1.25,
    "london": 1.35,
    "australia": 1.30,
    "sydney": 1.35,
    "india": 0.35,
    "mumbai": 0.35,
}

# Project-type factors.
_PROJECT_TYPE_FACTORS: Dict[str, float] = {
    "general_building": 1.0,
    "building": 1.0,
    "data_center": 1.15,
    "solar_plant": 0.95,
    "wind_farm": 1.05,
    "infrastructure": 0.95,
    "industrial": 1.10,
    "residential": 0.95,
    "commercial": 1.0,
    "healthcare": 1.20,
}


def _normalize(text: str) -> str:
    return (
        (text or "")
        .lower()
        .replace("/", " ")
        .replace("-", " ")
        .replace("_", " ")
        .replace("  ", " ")
        .strip()
    )


def _match_item(name: str) -> Optional[str]:
    norm = _normalize(name)
    # Exact key match first.
    if norm in _BASE_RATES:
        return norm
    # Substring match: item name contains a known key.
    for key in _BASE_RATES:
        if key in norm or norm in key:
            return key
    return None


def _location_factor(location: str) -> float:
    norm = _normalize(location)
    return _LOCATION_FACTORS.get(norm, 1.0)


def _project_factor(project_type: str) -> float:
    return _PROJECT_TYPE_FACTORS.get(_normalize(project_type), 1.0)


class HistoricalBenchmarkBlock(UniversalBlock):
    name = "historical_benchmark"
    version = "1.0.0"
    description = "Lightweight construction unit-rate benchmark source with regional factors"
    layer = 2
    tags = ["construction", "cost", "benchmark", "unit-rates"]
    requires = []

    default_config = {
        "confidence": "medium",
        "source_note": "Fallback static rate book — validate with supplier quotes.",
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        action = params.get("action") or input_data.get("action") if isinstance(input_data, dict) else None

        if action == "record" and isinstance(input_data, dict):
            # Future: persist user-supplied rates to learning_engine.
            return {
                "status": "success",
                "action": "record",
                "message": "Rate sample recorded (stub — learning_engine integration pending).",
            }

        item = params.get("item") or (input_data.get("item") if isinstance(input_data, dict) else "")
        unit = params.get("unit") or (input_data.get("unit") if isinstance(input_data, dict) else "")
        location = params.get("location") or (input_data.get("location") if isinstance(input_data, dict) else "US National Average")
        project_type = params.get("project_type") or (input_data.get("project_type") if isinstance(input_data, dict) else "general_building")

        key = _match_item(item)
        if not key:
            return {
                "status": "error",
                "action": "lookup",
                "item": item,
                "error": f"No benchmark rate available for '{item}'. Provide a supplier quote or add a custom rate.",
            }

        base = _BASE_RATES[key]
        loc_factor = _location_factor(location)
        proj_factor = _project_factor(project_type)
        adjusted = round(base["base_usd"] * loc_factor * proj_factor, 2)

        return {
            "status": "success",
            "action": "lookup",
            "item": item,
            "matched_key": key,
            "unit": unit or base.get("unit"),
            "rates": {
                "base_usd": base["base_usd"],
                "adjusted_usd": adjusted,
            },
            "factors": {
                "location_factor": loc_factor,
                "project_type_factor": proj_factor,
                "location": location,
                "project_type": project_type,
            },
            "note": base.get("note", ""),
            "source_note": self.config.get("source_note"),
            "confidence": self.config.get("confidence"),
        }
