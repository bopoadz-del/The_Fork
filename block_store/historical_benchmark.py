"""Historical Benchmark Block - RS Means / Diriyah project data lookup with PostgreSQL + in-memory fallback"""

import os
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock


# In-memory RS Means-style benchmark data (USD, Middle East market adjusted)
_BENCHMARK_DATA: Dict[str, Dict] = {
    # Concrete
    "concrete_c25": {"avg_cost": 1100, "std_dev": 120, "unit": "m³", "typical_variance": 0.11, "package": "concrete"},
    "concrete_c30": {"avg_cost": 1250, "std_dev": 145, "unit": "m³", "typical_variance": 0.12, "package": "concrete"},
    "concrete_c35": {"avg_cost": 1380, "std_dev": 155, "unit": "m³", "typical_variance": 0.11, "package": "concrete"},
    "concrete_c40": {"avg_cost": 1450, "std_dev": 168, "unit": "m³", "typical_variance": 0.12, "package": "concrete"},
    "concrete_c50": {"avg_cost": 1700, "std_dev": 190, "unit": "m³", "typical_variance": 0.11, "package": "concrete"},
    # Rebar / Steel
    "rebar_12mm": {"avg_cost": 2.8, "std_dev": 0.35, "unit": "kg", "typical_variance": 0.13, "package": "rebar"},
    "rebar_16mm": {"avg_cost": 2.9, "std_dev": 0.33, "unit": "kg", "typical_variance": 0.11, "package": "rebar"},
    "rebar_20mm": {"avg_cost": 3.1, "std_dev": 0.38, "unit": "kg", "typical_variance": 0.12, "package": "rebar"},
    "rebar_25mm": {"avg_cost": 3.2, "std_dev": 0.40, "unit": "kg", "typical_variance": 0.13, "package": "rebar"},
    "rebar_mixed": {"avg_cost": 3.0, "std_dev": 0.36, "unit": "kg", "typical_variance": 0.12, "package": "rebar"},
    "structural_steel": {"avg_cost": 4.5, "std_dev": 0.55, "unit": "kg", "typical_variance": 0.12, "package": "steel"},
    "steel_plate": {"avg_cost": 5.2, "std_dev": 0.65, "unit": "kg", "typical_variance": 0.13, "package": "steel"},
    # Formwork
    "formwork_slab": {"avg_cost": 42, "std_dev": 8, "unit": "m²", "typical_variance": 0.19, "package": "formwork"},
    "formwork_wall": {"avg_cost": 52, "std_dev": 9, "unit": "m²", "typical_variance": 0.17, "package": "formwork"},
    "formwork_column": {"avg_cost": 65, "std_dev": 12, "unit": "m²", "typical_variance": 0.18, "package": "formwork"},
    # Masonry
    "block_work_200mm": {"avg_cost": 88, "std_dev": 12, "unit": "m²", "typical_variance": 0.14, "package": "masonry"},
    "block_work_100mm": {"avg_cost": 72, "std_dev": 10, "unit": "m²", "typical_variance": 0.14, "package": "masonry"},
    "brick_facing": {"avg_cost": 145, "std_dev": 22, "unit": "m²", "typical_variance": 0.15, "package": "masonry"},
    # Finishes
    "plaster_internal": {"avg_cost": 32, "std_dev": 5, "unit": "m²", "typical_variance": 0.16, "package": "finishes"},
    "paint_internal": {"avg_cost": 14, "std_dev": 2.5, "unit": "m²", "typical_variance": 0.18, "package": "finishes"},
    "paint_external": {"avg_cost": 22, "std_dev": 4, "unit": "m²", "typical_variance": 0.18, "package": "finishes"},
    "flooring_porcelain": {"avg_cost": 185, "std_dev": 35, "unit": "m²", "typical_variance": 0.19, "package": "finishes"},
    "flooring_marble": {"avg_cost": 350, "std_dev": 70, "unit": "m²", "typical_variance": 0.20, "package": "finishes"},
    "ceiling_gypsum": {"avg_cost": 72, "std_dev": 12, "unit": "m²", "typical_variance": 0.17, "package": "finishes"},
    # Glass / Cladding
    "glass_curtain_wall": {"avg_cost": 480, "std_dev": 85, "unit": "m²", "typical_variance": 0.18, "package": "cladding"},
    "aluminum_cladding": {"avg_cost": 380, "std_dev": 65, "unit": "m²", "typical_variance": 0.17, "package": "cladding"},
    "stone_cladding": {"avg_cost": 520, "std_dev": 95, "unit": "m²", "typical_variance": 0.18, "package": "cladding"},
    # Insulation
    "insulation_roof": {"avg_cost": 35, "std_dev": 6, "unit": "m²", "typical_variance": 0.17, "package": "waterproofing"},
    "waterproofing_basement": {"avg_cost": 85, "std_dev": 18, "unit": "m²", "typical_variance": 0.21, "package": "waterproofing"},
    "waterproofing_roof": {"avg_cost": 65, "std_dev": 12, "unit": "m²", "typical_variance": 0.18, "package": "waterproofing"},
    # MEP
    "electrical_rough": {"avg_cost": 68, "std_dev": 12, "unit": "m²", "typical_variance": 0.18, "package": "electrical"},
    "electrical_fit_out": {"avg_cost": 95, "std_dev": 18, "unit": "m²", "typical_variance": 0.19, "package": "electrical"},
    "plumbing_rough": {"avg_cost": 88, "std_dev": 16, "unit": "m²", "typical_variance": 0.18, "package": "plumbing"},
    "hvac_ductwork": {"avg_cost": 125, "std_dev": 22, "unit": "m²", "typical_variance": 0.18, "package": "hvac"},
    "fire_fighting": {"avg_cost": 55, "std_dev": 10, "unit": "m²", "typical_variance": 0.18, "package": "fire"},
    # Earthworks
    "excavation_bulk": {"avg_cost": 18, "std_dev": 4, "unit": "m³", "typical_variance": 0.22, "package": "earthworks"},
    "backfill_compacted": {"avg_cost": 28, "std_dev": 6, "unit": "m³", "typical_variance": 0.21, "package": "earthworks"},
    "piling_bored_600mm": {"avg_cost": 750, "std_dev": 120, "unit": "m", "typical_variance": 0.16, "package": "piling"},
    "piling_bored_900mm": {"avg_cost": 1450, "std_dev": 220, "unit": "m", "typical_variance": 0.15, "package": "piling"},
    # Diriyah / Saudi specific
    "stone_limestone": {"avg_cost": 420, "std_dev": 75, "unit": "m²", "typical_variance": 0.18, "package": "heritage"},
    "nadji_style_plaster": {"avg_cost": 180, "std_dev": 35, "unit": "m²", "typical_variance": 0.19, "package": "heritage"},
    "traditional_woodwork": {"avg_cost": 2800, "std_dev": 550, "unit": "m²", "typical_variance": 0.20, "package": "heritage"},
}


class HistoricalBenchmarkBlock(UniversalBlock):
    name = "historical_benchmark"
    version = "1.0.0"
    description = "RS Means / Diriyah project data lookup: avg_cost, std_dev, variance by item and package"
    layer = 3
    tags = ["domain", "construction", "benchmark", "rs_means", "data", "cost"]
    requires = []

    default_config = {
        "use_postgres": False,
        "postgres_table": "historical_benchmarks",
        "fuzzy_match": True,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"item_key": "concrete_c30", "package_type": "concrete"}',
            "multiline": True,
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "avg_cost", "type": "number", "label": "Avg Cost"},
                {"name": "std_dev", "type": "number", "label": "Std Dev"},
                {"name": "typical_variance", "type": "percentage", "label": "Typical Variance"},
                {"name": "unit", "type": "text", "label": "Unit"},
            ],
        },
        "quick_actions": [
            {"icon": "💰", "label": "Lookup Rate", "prompt": "What is the benchmark rate for concrete C30?"},
            {"icon": "📦", "label": "Package Rates", "prompt": "Show all rates for the concrete package"},
            {"icon": "📊", "label": "All Benchmarks", "prompt": "Show all available benchmark items"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        item_key = data.get("item_key") or params.get("item_key", "")
        package_type = data.get("package_type") or params.get("package_type", "")
        operation = data.get("operation") or params.get("operation", "lookup")

        if operation == "list_all" or item_key.strip().lower() in ("all", "list", ""):
            return self._list_all(package_type)

        if operation == "package" or (not item_key and package_type):
            return self._lookup_package(package_type)

        # Try DB first if configured
        if self.config.get("use_postgres", False):
            db_result = await self._lookup_postgres(item_key, package_type)
            if db_result:
                return {"status": "success", **db_result}

        # In-memory lookup
        result = self._lookup_memory(item_key, package_type)
        if result:
            return {"status": "success", **result}

        # Fuzzy match
        if self.config.get("fuzzy_match", True):
            fuzzy = self._fuzzy_lookup(item_key)
            if fuzzy:
                fuzzy["matched_key"] = fuzzy.pop("_key", item_key)
                fuzzy["fuzzy_match"] = True
                return {"status": "success", **fuzzy}

        return {
            "status": "error",
            "error": f"No benchmark found for '{item_key}'. Try list_all to see available items.",
            "available_packages": list({v["package"] for v in _BENCHMARK_DATA.values()}),
        }

    def _lookup_memory(self, item_key: str, package_type: str) -> Optional[Dict]:
        key = item_key.lower().replace(" ", "_").replace("-", "_")
        data = _BENCHMARK_DATA.get(key)
        if data:
            if package_type and data.get("package") != package_type:
                return None
            return {**data, "item_key": key, "source": "in_memory"}
        return None

    def _fuzzy_lookup(self, item_key: str) -> Optional[Dict]:
        key_lower = item_key.lower().replace("-", "_")
        best_score = 0
        best_key = None
        for k in _BENCHMARK_DATA:
            score = sum(1 for word in key_lower.split("_") if word and word in k)
            if score > best_score:
                best_score = score
                best_key = k
        if best_key and best_score > 0:
            return {**_BENCHMARK_DATA[best_key], "item_key": best_key, "_key": best_key, "source": "fuzzy_match"}
        return None

    def _lookup_package(self, package_type: str) -> Dict:
        items = {
            k: v for k, v in _BENCHMARK_DATA.items()
            if v.get("package") == package_type or package_type == ""
        }
        if not items:
            return {
                "status": "error",
                "error": f"No items for package '{package_type}'",
                "available_packages": list({v["package"] for v in _BENCHMARK_DATA.values()}),
            }
        avg_costs = [v["avg_cost"] for v in items.values()]
        return {
            "status": "success",
            "package_type": package_type,
            "item_count": len(items),
            "items": {k: {**v, "item_key": k} for k, v in items.items()},
            "package_avg_cost": round(sum(avg_costs) / len(avg_costs), 2),
            "source": "in_memory",
        }

    def _list_all(self, package_filter: str = "") -> Dict:
        items = _BENCHMARK_DATA
        if package_filter:
            items = {k: v for k, v in items.items() if v.get("package") == package_filter}
        packages = {}
        for k, v in items.items():
            pkg = v["package"]
            if pkg not in packages:
                packages[pkg] = []
            packages[pkg].append(k)
        return {
            "status": "success",
            "total_items": len(items),
            "packages": packages,
            "items": {k: {**v, "item_key": k} for k, v in items.items()},
            "source": "in_memory",
        }

    async def _lookup_postgres(self, item_key: str, package_type: str) -> Optional[Dict]:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return None
        try:
            import asyncpg
            conn = await asyncpg.connect(db_url)
            table = self.config.get("postgres_table", "historical_benchmarks")
            row = await conn.fetchrow(
                f"SELECT * FROM {table} WHERE item_key = $1", item_key
            )
            await conn.close()
            if row:
                return dict(row)
        except Exception:
            pass
        return None
