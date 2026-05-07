"""
Libraries Container - Unified data, formula, and domain library hub

Groups all data-lookup, formula-execution, ML, and template blocks.
Supports 100+ domain libraries (CoolProp, QuantLib, scipy, pandas, etc.)
via a plugin-style dynamic loader.
"""

import os
import importlib
from typing import Any, Dict, List, Optional

from app.core.universal_base import UniversalContainer


# ── Domain library registry ────────────────────────────────────────────────────
# Each entry: module_name → (pip_name, description, category)
DOMAIN_LIBRARY_REGISTRY: Dict[str, tuple] = {
    # Math / Science
    "sympy":        ("sympy",              "Symbolic mathematics",                        "math"),
    "numpy":        ("numpy",              "Numerical computing",                         "math"),
    "scipy":        ("scipy",              "Scientific computing",                        "math"),
    "mpmath":       ("mpmath",             "Arbitrary-precision arithmetic",              "math"),
    # Data
    "pandas":       ("pandas",             "DataFrame / tabular data",                    "data"),
    "openpyxl":     ("openpyxl",           "Excel read/write",                           "data"),
    "xlrd":         ("xlrd",               "Legacy XLS reading",                         "data"),
    "pyarrow":      ("pyarrow",            "Apache Arrow / Parquet",                     "data"),
    "polars":       ("polars",             "Fast DataFrame (Rust-backed)",                "data"),
    # Units
    "pint":         ("pint",               "Unit conversion & dimensional analysis",      "units"),
    # Engineering / Construction
    "ezdxf":        ("ezdxf",              "DXF/DWG drawing parsing",                    "aec"),
    "shapely":      ("shapely",            "2D geometry / area calculations",             "aec"),
    "ifcopenshell": ("ifcopenshell",       "IFC BIM model processing",                   "aec"),
    "triangle":     ("triangle",           "Delaunay triangulation",                     "aec"),
    "sectionproperties": ("sectionproperties", "Structural section properties",          "aec"),
    # Finance / Quant
    "quantlib":     ("QuantLib-Python",    "Quantitative finance library",               "finance"),
    "pyfolio":      ("pyfolio",            "Portfolio performance analytics",            "finance"),
    "ta":           ("ta",                 "Technical analysis indicators",              "finance"),
    "riskfolio":    ("Riskfolio-Lib",      "Portfolio optimization",                     "finance"),
    # Thermodynamics / HVAC
    "coolprop":     ("CoolProp",           "Fluid & thermodynamic properties",           "hvac"),
    "psychrolib":   ("PsychroLib",         "Psychrometric calculations",                 "hvac"),
    # Geotechnical
    "geopy":        ("geopy",              "Geocoding / geodesic distance",              "geotech"),
    "pyproj":       ("pyproj",             "Cartographic projection",                    "geotech"),
    "fiona":        ("fiona",              "GIS vector data I/O",                        "geotech"),
    "rasterio":     ("rasterio",           "Raster GIS data",                           "geotech"),
    # ML / Statistics
    "sklearn":      ("scikit-learn",       "Machine learning",                           "ml"),
    "xgboost":      ("xgboost",            "Gradient boosting",                         "ml"),
    "lightgbm":     ("lightgbm",           "Fast gradient boosting",                    "ml"),
    "statsmodels":  ("statsmodels",        "Statistical modeling & econometrics",        "ml"),
    # PDF / Documents
    "fitz":         ("pymupdf",            "PDF text & image extraction",                "docs"),
    "pdfplumber":   ("pdfplumber",         "PDF table extraction",                       "docs"),
    "docx":         ("python-docx",        "Word document processing",                   "docs"),
    "reportlab":    ("reportlab",          "PDF generation",                            "docs"),
    # Image / Vision
    "cv2":          ("opencv-python",      "Computer vision",                            "vision"),
    "PIL":          ("pillow",             "Image processing",                           "vision"),
    "skimage":      ("scikit-image",       "Image analysis",                            "vision"),
    # NLP
    "spacy":        ("spacy",              "NLP: named entity recognition",              "nlp"),
    "nltk":         ("nltk",               "Natural language toolkit",                   "nlp"),
    "transformers": ("transformers",       "HuggingFace transformers",                   "nlp"),
    # Serialization
    "yaml":         ("PyYAML",             "YAML parsing",                              "util"),
    "toml":         ("tomllib",            "TOML parsing",                              "util"),
    "orjson":       ("orjson",             "Fast JSON serialization",                   "util"),
    # Monitoring
    "mlflow":       ("mlflow",             "ML experiment tracking",                    "mlops"),
    "prometheus_client": ("prometheus-client", "Prometheus metrics",                   "mlops"),
}


class LibrariesContainer(UniversalContainer):
    """
    Libraries Container: unified data, formula, and domain library hub.

    Routes to:
    - sympy_reasoning  → symbolic variance analysis
    - boq_processor    → Excel/CSV BOQ parsing
    - spec_analyzer    → PDF spec extraction
    - formula_executor → chat-to-code formula execution
    - historical_benchmark → RS Means cost lookup
    - recommendation_template → rule-based recommendations
    - learning_engine  → tier promotion + coefficient tuning
    - drawing_qto      → DXF quantity take-off
    - primavera_parser → XER schedule parsing
    - bim_extractor    → IFC BIM extraction
    - library_info     → domain library registry info
    - library_compute  → dynamic library invocation
    """

    name = "libraries"
    version = "1.0.0"
    description = "Unified data, formula, and domain library hub: math, units, BIM, cost benchmarks, ML, NLP, and 100+ domain libraries"
    layer = 3
    tags = ["container", "libraries", "data", "formulas", "math", "domain"]
    requires = [
        "sympy_reasoning",
        "boq_processor",
        "spec_analyzer",
        "formula_executor",
        "historical_benchmark",
        "recommendation_template",
        "learning_engine",
        "drawing_qto",
        "primavera_parser",
        "bim_extractor",
    ]

    default_config = {
        "allow_dynamic_import": True,
        "sandbox_compute": True,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": (
                '{"action": "formula_execute", "formula_description": "concrete volume", '
                '"input_values": {"length_m": 10, "width_m": 8, "thickness_m": 0.2}}'
            ),
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "result", "type": "json", "label": "Result"},
                {"name": "library_used", "type": "text", "label": "Library"},
            ],
        },
        "quick_actions": [
            {"icon": "🧮", "label": "Run Formula", "prompt": "Calculate concrete volume for a 10x8m slab 200mm thick"},
            {"icon": "💰", "label": "Benchmark Lookup", "prompt": "What is the benchmark rate for concrete C30?"},
            {"icon": "📚", "label": "Library Info", "prompt": "List all available domain libraries"},
            {"icon": "📊", "label": "BOQ Parse", "prompt": "Parse and summarize this Bill of Quantities"},
        ],
    }

    # ── Route table ────────────────────────────────────────────────────────────

    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        action = data.get("action") or p.get("action") or action

        handlers = {
            # Formula & Math
            "sympy_reason":        self._delegate("sympy_reasoning"),
            "formula_execute":     self._delegate("formula_executor"),
            "formula_list":        self._formula_list,
            # BOQ / Data
            "boq_process":         self._delegate("boq_processor"),
            # Specs
            "spec_analyze":        self._delegate("spec_analyzer"),
            # Benchmarks
            "benchmark_lookup":    self._delegate("historical_benchmark"),
            "benchmark_list":      self._benchmark_list,
            # Recommendations
            "recommend":           self._delegate("recommendation_template"),
            "rule_list":           self._rule_list,
            # ML / Learning
            "learn":               self._delegate("learning_engine"),
            "tier_status":         self._tier_status,
            # Drawings
            "drawing_qto":         self._delegate("drawing_qto"),
            # Schedule
            "primavera_parse":     self._delegate("primavera_parser"),
            # BIM
            "bim_extract":         self._delegate("bim_extractor"),
            # Domain Libraries
            "library_info":        self._library_info,
            "library_compute":     self._library_compute,
            "library_check":       self._library_check,
            # Meta
            "health_check":        self._health_check,
            "list_actions":        self._list_actions,
        }

        handler = handlers.get(action)
        if not handler:
            return {
                "status": "error",
                "error": f"Unknown library action: '{action}'",
                "available_actions": list(handlers.keys()),
            }

        if callable(handler) and not asyncio_iscoroutinefunction(handler):
            result = handler(input_data, params)
        else:
            result = await handler(input_data, params)
        return result

    # ── Delegation helpers ─────────────────────────────────────────────────────

    def _delegate(self, block_name: str):
        """Return an async handler that delegates to a registered block."""
        async def _handler(input_data: Any, params: Dict) -> Dict:
            from app.blocks import BLOCK_REGISTRY
            block_cls = BLOCK_REGISTRY.get(block_name)
            if not block_cls:
                return {"status": "error", "error": f"Block '{block_name}' not registered"}
            instance = block_cls()
            return await instance.process(input_data, params)
        return _handler

    # ── Formula helpers ────────────────────────────────────────────────────────

    async def _formula_list(self, input_data: Any, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("formula_executor")
        if not cls:
            return {"status": "error", "error": "formula_executor not registered"}
        instance = cls()
        return await instance.process({"operation": "list"}, params)

    # ── Benchmark helpers ──────────────────────────────────────────────────────

    async def _benchmark_list(self, input_data: Any, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("historical_benchmark")
        if not cls:
            return {"status": "error", "error": "historical_benchmark not registered"}
        instance = cls()
        return await instance.process({"operation": "list_all"}, params)

    # ── Rule helpers ───────────────────────────────────────────────────────────

    async def _rule_list(self, input_data: Any, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("recommendation_template")
        if not cls:
            return {"status": "error", "error": "recommendation_template not registered"}
        instance = cls()
        return await instance.process({"operation": "list_rules"}, params)

    # ── Learning helpers ───────────────────────────────────────────────────────

    async def _tier_status(self, input_data: Any, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("learning_engine")
        if not cls:
            return {"status": "error", "error": "learning_engine not registered"}
        instance = cls()
        return await instance.process({"operation": "status"}, params)

    # ── Domain library actions ─────────────────────────────────────────────────

    async def _library_info(self, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        category_filter = data.get("category") or (params or {}).get("category", "")

        by_category: Dict[str, List[Dict]] = {}
        for mod_name, (pip_name, description, category) in DOMAIN_LIBRARY_REGISTRY.items():
            if category_filter and category != category_filter:
                continue
            if category not in by_category:
                by_category[category] = []
            by_category[category].append({
                "module": mod_name,
                "pip_install": pip_name,
                "description": description,
                "available": self._is_available(mod_name),
            })

        total = sum(len(v) for v in by_category.values())
        available = sum(
            1 for libs in by_category.values()
            for lib in libs if lib["available"]
        )

        return {
            "status": "success",
            "total_libraries": total,
            "available_libraries": available,
            "categories": by_category,
            "category_list": list(by_category.keys()),
        }

    async def _library_check(self, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        module_name = data.get("module") or (params or {}).get("module", "")
        if not module_name:
            return {"status": "error", "error": "module name required"}

        info = DOMAIN_LIBRARY_REGISTRY.get(module_name, (module_name, "unknown", "custom"))
        available = self._is_available(module_name)
        version = ""
        if available:
            try:
                mod = importlib.import_module(module_name)
                version = getattr(mod, "__version__", "")
            except Exception:
                pass

        return {
            "status": "success",
            "module": module_name,
            "pip_install": info[0],
            "description": info[1],
            "available": available,
            "version": version,
        }

    async def _library_compute(self, input_data: Any, params: Dict) -> Dict:
        """
        Dynamically invoke a domain library function.
        Input: {"library": "coolprop", "function": "PropsSI", "args": ["H", "T", 300, "P", 101325, "Water"]}
        """
        if not self.config.get("allow_dynamic_import", True):
            return {"status": "error", "error": "Dynamic library compute disabled in config"}

        data = input_data if isinstance(input_data, dict) else {}
        library = data.get("library") or (params or {}).get("library", "")
        function = data.get("function") or (params or {}).get("function", "")
        args = data.get("args", [])
        kwargs = data.get("kwargs", {})

        if not library or not function:
            return {"status": "error", "error": "library and function are required"}

        if not self._is_available(library):
            pip_name = DOMAIN_LIBRARY_REGISTRY.get(library, (library,))[0]
            return {
                "status": "error",
                "error": f"Library '{library}' not installed. Run: pip install {pip_name}",
            }

        try:
            mod = importlib.import_module(library)
            # Allow dot-notation: "CoolProp.CoolProp"
            for part in function.split("."):
                mod = getattr(mod, part)
            result = mod(*args, **kwargs)
            return {
                "status": "success",
                "library": library,
                "function": function,
                "result": result if isinstance(result, (int, float, str, list, dict, bool)) else str(result),
                "args": args,
            }
        except AttributeError:
            return {"status": "error", "error": f"Function '{function}' not found in '{library}'"}
        except Exception as e:
            return {"status": "error", "error": f"Compute error: {e}"}

    # ── Health & meta ──────────────────────────────────────────────────────────

    async def _health_check(self, input_data: Any, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        sub_status = {}
        for block_name in self.requires:
            sub_status[block_name] = "registered" if block_name in BLOCK_REGISTRY else "missing"

        available_libs = sum(1 for mod in DOMAIN_LIBRARY_REGISTRY if self._is_available(mod))
        return {
            "status": "success",
            "container": self.name,
            "version": self.version,
            "sub_blocks": sub_status,
            "domain_libraries_available": available_libs,
            "domain_libraries_total": len(DOMAIN_LIBRARY_REGISTRY),
        }

    async def _list_actions(self, input_data: Any, params: Dict) -> Dict:
        return {
            "status": "success",
            "actions": {
                "formula_math": ["sympy_reason", "formula_execute", "formula_list"],
                "data_parsing": ["boq_process", "spec_analyze", "drawing_qto", "primavera_parse", "bim_extract"],
                "benchmarks": ["benchmark_lookup", "benchmark_list"],
                "recommendations": ["recommend", "rule_list"],
                "machine_learning": ["learn", "tier_status"],
                "domain_libraries": ["library_info", "library_check", "library_compute"],
                "meta": ["health_check", "list_actions"],
            },
        }

    def _is_available(self, module_name: str) -> bool:
        try:
            importlib.import_module(module_name)
            return True
        except ImportError:
            return False


def asyncio_iscoroutinefunction(func) -> bool:
    import asyncio
    return asyncio.iscoroutinefunction(func)
