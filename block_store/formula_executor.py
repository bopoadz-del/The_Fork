"""Formula Executor Block - Chat-to-code generation + sandboxed execution with unit validation"""

import os
import math
import traceback
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock


# Safe builtins allowed inside sandbox
_SAFE_BUILTINS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "len": len, "range": range, "enumerate": enumerate,
    "zip": zip, "sorted": sorted, "reversed": reversed,
    "int": int, "float": float, "str": str, "bool": bool, "list": list,
    "dict": dict, "tuple": tuple, "set": set,
    "print": print, "type": type, "isinstance": isinstance,
    "__builtins__": {},
}

_SAFE_MODULES = {
    "math": math,
}


class FormulaExecutorBlock(UniversalBlock):
    name = "formula_executor"
    version = "1.0.0"
    description = "Chat-to-code: generate Python formulas from description, execute in sandbox with unit validation"
    layer = 3
    tags = ["domain", "construction", "formula", "math", "code", "units", "sandbox"]
    requires = []

    default_config = {
        "max_code_lines": 50,
        "timeout_seconds": 10,
        "allow_sympy": True,
        "allow_numpy": True,
    }

    # Built-in construction formula library
    _FORMULA_LIBRARY: Dict[str, Dict] = {
        "concrete_volume_column": {
            "description": "Rectangular column concrete volume",
            "params": {"width_m": 0.5, "depth_m": 0.5, "height_m": 3.0, "count": 1},
            "code": "result = width_m * depth_m * height_m * count",
            "unit": "m³",
        },
        "concrete_volume_slab": {
            "description": "Flat slab concrete volume",
            "params": {"length_m": 10.0, "width_m": 8.0, "thickness_m": 0.2},
            "code": "result = length_m * width_m * thickness_m",
            "unit": "m³",
        },
        "rebar_weight": {
            "description": "Rebar weight from diameter and length",
            "params": {"diameter_mm": 16.0, "length_m": 100.0},
            "code": "result = (diameter_mm ** 2 / 162.27) * length_m",
            "unit": "kg",
        },
        "formwork_area_wall": {
            "description": "Wall formwork area (both faces)",
            "params": {"length_m": 10.0, "height_m": 3.0},
            "code": "result = length_m * height_m * 2",
            "unit": "m²",
        },
        "paint_area": {
            "description": "Paintable wall area minus openings",
            "params": {"perimeter_m": 40.0, "height_m": 2.8, "opening_m2": 15.0},
            "code": "result = perimeter_m * height_m - opening_m2",
            "unit": "m²",
        },
        "steel_beam_weight": {
            "description": "Steel beam weight from kg/m and span",
            "params": {"kg_per_m": 74.0, "span_m": 8.0, "count": 1},
            "code": "result = kg_per_m * span_m * count",
            "unit": "kg",
        },
        "brick_count": {
            "description": "Brick count for wall",
            "params": {"wall_area_m2": 50.0, "brick_size": "230x110x70", "mortar_mm": 10},
            "code": (
                "w, h, d = [int(x) for x in brick_size.split('x')]\n"
                "bricks_per_m2 = (1000 / (w + mortar_mm)) * (1000 / (d + mortar_mm))\n"
                "result = math.ceil(wall_area_m2 * bricks_per_m2)"
            ),
            "unit": "pcs",
        },
        "concrete_cost": {
            "description": "Concrete cost estimate",
            "params": {"volume_m3": 100.0, "rate_per_m3": 1250.0, "waste_factor": 1.05},
            "code": "result = volume_m3 * rate_per_m3 * waste_factor",
            "unit": "USD",
        },
        "carbon_concrete": {
            "description": "Embodied carbon for concrete",
            "params": {"volume_m3": 100.0, "grade": "C30", "density_kg_m3": 2400},
            "code": (
                "factors = {'C25': 0.29, 'C30': 0.35, 'C35': 0.38, 'C40': 0.42}\n"
                "ecf = factors.get(grade, 0.35)\n"
                "result = volume_m3 * density_kg_m3 * ecf"
            ),
            "unit": "kgCO2e",
        },
        "earned_value": {
            "description": "Earned Value Management: SPI, CPI, EAC",
            "params": {"bac": 1000000.0, "pv": 500000.0, "ev": 450000.0, "ac": 480000.0},
            "code": (
                "spi = ev / pv if pv else 0\n"
                "cpi = ev / ac if ac else 0\n"
                "eac = bac / cpi if cpi else bac\n"
                "vac = bac - eac\n"
                "result = {'spi': round(spi,3), 'cpi': round(cpi,3), 'eac': round(eac,2), 'vac': round(vac,2)}"
            ),
            "unit": "dimensionless",
        },
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"formula_description": "Calculate concrete volume for a slab", "input_values": {"length_m": 10, "width_m": 8, "thickness_m": 0.2}}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "generated_code", "type": "code", "label": "Generated Code"},
                {"name": "execution_result", "type": "text", "label": "Result"},
                {"name": "unit_validated_output", "type": "json", "label": "Unit Output"},
            ],
        },
        "quick_actions": [
            {"icon": "🧮", "label": "Calculate", "prompt": "Calculate concrete volume for a 10x8m slab, 200mm thick"},
            {"icon": "📚", "label": "Formula Library", "prompt": "Show all available construction formulas"},
            {"icon": "💰", "label": "Cost Formula", "prompt": "Calculate total cost from quantities and rates"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        description = data.get("formula_description") or params.get("formula_description", "")
        input_values = data.get("input_values", {})
        input_values.update(params.get("input_values", {}))
        formula_key = data.get("formula_key") or params.get("formula_key")
        operation = data.get("operation") or params.get("operation", "auto")

        if operation == "list" or description.strip().lower() in ("list", "library", "show formulas"):
            return self._list_formulas()

        # Try library lookup first
        if formula_key and formula_key in self._FORMULA_LIBRARY:
            return await self._run_library_formula(formula_key, input_values)

        # Try fuzzy library match from description
        matched_key = self._match_library(description)
        if matched_key and not data.get("custom_code"):
            return await self._run_library_formula(matched_key, input_values)

        # Generate and execute custom formula
        custom_code = data.get("custom_code") or params.get("custom_code")
        if not custom_code:
            custom_code = self._generate_formula(description, input_values)

        return await self._execute_sandbox(custom_code, input_values, description)

    async def _run_library_formula(self, key: str, input_values: Dict) -> Dict:
        formula = self._FORMULA_LIBRARY[key]
        merged = {**formula["params"], **input_values}
        result = await self._execute_sandbox(formula["code"], merged, formula["description"])
        result["formula_key"] = key
        result["formula_description"] = formula["description"]
        result["unit"] = formula["unit"]
        return result

    async def _execute_sandbox(
        self, code: str, variables: Dict, description: str
    ) -> Dict:
        # Validate code length
        lines = [l for l in code.strip().splitlines() if l.strip()]
        max_lines = int(self.config.get("max_code_lines", 50))
        if len(lines) > max_lines:
            return {
                "status": "error",
                "error": f"Code too long: {len(lines)} lines (max {max_lines})",
            }

        # Build safe execution namespace
        namespace = {**_SAFE_BUILTINS, "math": math}

        if self.config.get("allow_sympy", True):
            try:
                import sympy
                namespace["sympy"] = sympy
                namespace["sp"] = sympy
            except ImportError:
                pass

        if self.config.get("allow_numpy", True):
            try:
                import numpy
                namespace["np"] = numpy
                namespace["numpy"] = numpy
            except ImportError:
                pass

        # Inject input variables (numbers and strings only — no callables)
        for k, v in variables.items():
            if isinstance(v, (int, float, str, bool, list, dict)):
                namespace[k] = v

        # Add 'result' placeholder
        namespace["result"] = None

        try:
            exec(compile(code, "<formula>", "exec"), namespace)  # noqa: S102
        except Exception as e:
            return {
                "status": "error",
                "error": f"Execution error: {e}",
                "traceback": traceback.format_exc(limit=3),
                "generated_code": code,
                "input_values": variables,
            }

        raw_result = namespace.get("result")
        unit_output = self._validate_units(raw_result, variables, description)

        return {
            "status": "success",
            "generated_code": code,
            "execution_result": raw_result,
            "unit_validated_output": unit_output,
            "input_values": variables,
            "description": description,
        }

    def _validate_units(self, result: Any, variables: Dict, description: str) -> Dict:
        desc_lower = description.lower()
        unit = "unknown"
        if any(w in desc_lower for w in ("volume", "m3", "m³")):
            unit = "m³"
        elif any(w in desc_lower for w in ("area", "m2", "m²", "paint", "formwork")):
            unit = "m²"
        elif any(w in desc_lower for w in ("weight", "kg", "tonne", "rebar", "steel")):
            unit = "kg"
        elif any(w in desc_lower for w in ("cost", "price", "usd", "sar", "aed", "budget")):
            unit = "USD"
        elif any(w in desc_lower for w in ("carbon", "co2", "emission")):
            unit = "kgCO2e"
        elif any(w in desc_lower for w in ("length", "meter", "metre", "span")):
            unit = "m"
        elif any(w in desc_lower for w in ("count", "pieces", "number", "bricks")):
            unit = "pcs"

        return {
            "value": result,
            "unit": unit,
            "formatted": f"{result} {unit}" if isinstance(result, (int, float)) else str(result),
        }

    def _generate_formula(self, description: str, variables: Dict) -> str:
        desc_lower = description.lower()
        var_names = list(variables.keys())
        params_str = ", ".join(f"{k}={v}" for k, v in variables.items() if isinstance(v, (int, float)))

        # Simple pattern-based code generation
        if "volume" in desc_lower and all(k in variables for k in ["length_m", "width_m", "thickness_m"]):
            return "result = length_m * width_m * thickness_m"
        if "volume" in desc_lower and all(k in variables for k in ["width_m", "depth_m", "height_m"]):
            return "result = width_m * depth_m * height_m"
        if "area" in desc_lower and all(k in variables for k in ["length_m", "width_m"]):
            return "result = length_m * width_m"
        if "cost" in desc_lower and all(k in variables for k in ["quantity", "rate"]):
            return "result = quantity * rate"
        if "rebar" in desc_lower or "reinforcement" in desc_lower:
            if "diameter_mm" in variables and "length_m" in variables:
                return "result = (diameter_mm ** 2 / 162.27) * length_m"

        # Generic multiply all numeric inputs
        numeric_vars = [k for k, v in variables.items() if isinstance(v, (int, float))]
        if len(numeric_vars) >= 2:
            return f"result = {' * '.join(numeric_vars)}"
        if len(numeric_vars) == 1:
            return f"result = {numeric_vars[0]}"

        return f"# Auto-generated for: {description}\nresult = None  # Cannot auto-generate — provide custom_code"

    def _match_library(self, description: str) -> Optional[str]:
        desc_lower = description.lower()
        scores: Dict[str, int] = {}
        for key, formula in self._FORMULA_LIBRARY.items():
            score = 0
            fkey_words = key.replace("_", " ").split()
            fdesc_words = formula["description"].lower().split()
            for word in fkey_words + fdesc_words:
                if len(word) > 3 and word in desc_lower:
                    score += 1
            if score > 0:
                scores[key] = score
        if not scores:
            return None
        return max(scores, key=lambda k: scores[k])

    def _list_formulas(self) -> Dict:
        return {
            "status": "success",
            "formula_library": {
                k: {
                    "description": v["description"],
                    "params": list(v["params"].keys()),
                    "unit": v["unit"],
                }
                for k, v in self._FORMULA_LIBRARY.items()
            },
            "total_formulas": len(self._FORMULA_LIBRARY),
            "execution_result": None,
            "generated_code": "",
            "unit_validated_output": {},
        }
