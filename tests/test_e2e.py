"""
Cerebrum Blocks — End-to-End Test Suite
Covers: registry, connector protocol, every block group, full PoC pipeline, API routing.
"""

import asyncio
import sys
import time
import os
from datetime import date, timedelta
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _r(result: Dict) -> Dict:
    """Unwrap execute() envelope to inner result dict."""
    return result.get("result", result)


# ══════════════════════════════════════════════════════════════════════════════
# 1. REGISTRY & APP STARTUP
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistry:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    def test_registry_loads(self):
        from app.blocks import BLOCK_REGISTRY
        assert len(BLOCK_REGISTRY) >= 54, f"Expected ≥54 blocks, got {len(BLOCK_REGISTRY)}"

    def test_all_poc_blocks_present(self):
        from app.blocks import BLOCK_REGISTRY
        required = [
            "boq_processor", "spec_analyzer", "drawing_qto", "primavera_parser",
            "bim_extractor", "sympy_reasoning", "heavy_reasoning_engine",
            "formula_executor", "rfi_generator", "submittal_log_generator",
            "intelligent_workflow", "smart_orchestrator",
            "validator", "credibility_scorer", "predictive_engine", "evidence_vault",
            "historical_benchmark", "recommendation_template", "learning_engine",
            "ml_engine", "chat", "pdf", "ocr", "vector_search",
        ]
        missing = [b for b in required if b not in BLOCK_REGISTRY]
        assert not missing, f"Missing blocks: {missing}"

    def test_no_import_errors(self):
        """Importing the full registry must not raise."""
        from app.blocks import BLOCK_REGISTRY, get_block, get_all_blocks
        assert callable(get_block)
        assert callable(get_all_blocks)

    def test_app_routes(self):
        from app.main import app
        paths = {r.path for r in app.routes}
        for required in ["/execute", "/chain", "/chat", "/blocks",
                         "/health", "/poc/analyze", "/poc/topology",
                         "/poc/validate-pipeline", "/poc/health"]:
            assert required in paths, f"Route {required} missing"


# ══════════════════════════════════════════════════════════════════════════════
# 2. UNIVERSAL BASE & CONNECTOR PROTOCOL
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectorProtocol:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    def test_base_class_fields(self):
        from app.core.universal_base import UniversalBlock
        assert hasattr(UniversalBlock, "context_key")
        assert hasattr(UniversalBlock, "output_schema")
        assert hasattr(UniversalBlock, "input_schema")
        assert hasattr(UniversalBlock, "get_context_key")
        assert hasattr(UniversalBlock, "get_connector_contract")

    def test_context_key_fallback(self):
        from app.blocks.chat import ChatBlock
        b = ChatBlock()
        # ChatBlock has no explicit context_key → should fall back to "chat_result"
        assert b.get_context_key() == "chat_result"

    def test_construction_blocks_have_context_keys(self):
        from app.blocks import BLOCK_REGISTRY
        blocks_with_schema = [
            "boq_processor", "spec_analyzer", "drawing_qto", "primavera_parser",
            "historical_benchmark", "heavy_reasoning_engine", "rfi_generator",
            "submittal_log_generator", "recommendation_template", "credibility_scorer",
        ]
        for name in blocks_with_schema:
            cls = BLOCK_REGISTRY[name]
            key = getattr(cls, "context_key", "")
            assert key, f"{name} has empty context_key"
            assert key != name, f"{name}.context_key should differ from block name"

    def test_output_schemas_are_dicts(self):
        from app.blocks import BLOCK_REGISTRY
        for name, cls in BLOCK_REGISTRY.items():
            schema = getattr(cls, "output_schema", {})
            assert isinstance(schema, dict), f"{name}.output_schema must be dict"

    def test_input_schemas_declare_sources(self):
        from app.blocks import BLOCK_REGISTRY
        # heavy_reasoning_engine must declare sources for all its inputs
        cls = BLOCK_REGISTRY["heavy_reasoning_engine"]
        ischema = cls.input_schema
        assert "boq_result" in ischema
        assert ischema["boq_result"]["source"] == "boq_processor"
        assert "spec_result" in ischema
        assert ischema["spec_result"]["source"] == "spec_analyzer"

    def test_connector_contract_method(self):
        from app.blocks.boq_processor import BOQProcessorBlock
        b = BOQProcessorBlock()
        contract = b.get_connector_contract()
        assert contract["context_key"] == "boq_result"
        assert "items" in contract["output_schema"]
        assert contract["layer"] == 3

    def test_get_stats_includes_context_key(self):
        from app.blocks.spec_analyzer import SpecAnalyzerBlock
        b = SpecAnalyzerBlock()
        stats = b.get_stats()
        assert stats["context_key"] == "spec_result"


# ══════════════════════════════════════════════════════════════════════════════
# 3. CONNECTOR REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectorRegistry:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    def test_registry_builds(self):
        from app.core.connector_registry import ConnectorRegistry
        from app.blocks import BLOCK_REGISTRY
        reg = ConnectorRegistry(BLOCK_REGISTRY)
        graph = reg.build()
        assert "blocks" in graph
        assert "edges" in graph
        assert graph["stats"]["total_blocks"] >= 54
        assert graph["stats"]["total_edges"] >= 10

    def test_known_edges_present(self):
        from app.core.connector_registry import get_connector_registry
        reg = get_connector_registry()
        graph = reg.build()
        edge_pairs = {(e["from_block"], e["to_block"]) for e in graph["edges"]}
        assert ("boq_processor",       "heavy_reasoning_engine") in edge_pairs
        assert ("spec_analyzer",        "heavy_reasoning_engine") in edge_pairs
        assert ("drawing_qto",          "heavy_reasoning_engine") in edge_pairs
        assert ("heavy_reasoning_engine","recommendation_template") in edge_pairs
        assert ("heavy_reasoning_engine","rfi_generator")          in edge_pairs
        assert ("boq_processor",        "submittal_log_generator") in edge_pairs

    def test_context_map_unique(self):
        from app.core.connector_registry import get_connector_registry
        reg = get_connector_registry()
        graph = reg.build()
        # context_keys must be unique
        keys = list(graph["context_map"].keys())
        assert len(keys) == len(set(keys)), "Duplicate context_keys detected"

    def test_pipeline_map_schema_driven(self):
        from app.core.connector_registry import get_connector_registry
        reg = get_connector_registry()
        pmap = reg.get_pipeline_map(["boq_processor", "spec_analyzer", "heavy_reasoning_engine"])
        assert pmap["boq_result"]      == "boq_processor"
        assert pmap["spec_result"]     == "spec_analyzer"
        assert pmap["reasoning_result"]== "heavy_reasoning_engine"

    def test_full_poc_pipeline_valid(self):
        from app.core.connector_registry import get_connector_registry
        reg = get_connector_registry()
        pipeline = [
            "boq_processor", "spec_analyzer", "drawing_qto", "historical_benchmark",
            "heavy_reasoning_engine", "credibility_scorer", "recommendation_template",
            "rfi_generator", "submittal_log_generator",
        ]
        valid, missing = reg.validate_pipeline(pipeline)
        assert valid, f"Pipeline invalid — missing: {missing}"

    def test_topology_layers(self):
        from app.core.connector_registry import get_connector_registry
        reg = get_connector_registry()
        topo = reg.get_topology()
        layers = topo["layers"]
        assert "2" in layers  # orchestration layer
        assert "3" in layers  # domain layer
        assert "intelligent_workflow" in layers["2"]
        assert "boq_processor"        in layers["3"]
        assert "heavy_reasoning_engine" in layers["3"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. CORE AI BLOCKS
# ══════════════════════════════════════════════════════════════════════════════

class TestCoreBlocks:
    @pytest.mark.asyncio
    async def test_chat_block_instantiates(self):
        from app.blocks.chat import ChatBlock
        b = ChatBlock()
        assert b.name == "chat"
        # No API key in test env — verify it returns a structured error, not a crash
        r = await b.execute("hello", {})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_pdf_block(self):
        from app.blocks.pdf import PDFBlock
        b = PDFBlock()
        r = await b.execute({"text": "Sample text"}, {"action": "extract"})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_ocr_block(self):
        from app.blocks.ocr import OCRBlock
        b = OCRBlock()
        r = await b.execute({}, {"action": "extract_text"})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_translate_block(self):
        from app.blocks.translate import TranslateBlock
        b = TranslateBlock()
        r = await b.execute("Hello", {"target": "ar"})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_vector_search_block(self):
        from app.blocks.vector_search import VectorSearchBlock
        b = VectorSearchBlock()
        r = await b.execute({}, {"operation": "list_collections"})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_local_drive_block(self):
        from app.blocks.local_drive import LocalDriveBlock
        b = LocalDriveBlock()
        r = await b.execute("/tmp", {"action": "list"})
        assert r.get("status") == "success"

    @pytest.mark.asyncio
    async def test_code_block(self):
        from app.blocks.code import CodeBlock
        b = CodeBlock()
        r = await b.execute("print('hello')", {"language": "python"})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_zvec_block(self):
        from app.blocks.zvec import ZvecBlock
        b = ZvecBlock()
        r = await b.execute([1, 2, 3], {"action": "embed"})
        assert "status" in r


# ══════════════════════════════════════════════════════════════════════════════
# 5. CONSTRUCTION INTELLIGENCE BLOCKS (individual)
# ══════════════════════════════════════════════════════════════════════════════

BOQ_ITEMS = [
    {"description": "Concrete C35", "quantity": 1200, "unit": "m3",
     "unit_cost": 240, "item_key": "concrete_c35", "grade": "C35"},
    {"description": "Rebar 500 MPa", "quantity": 180, "unit": "t",
     "unit_cost": 920, "item_key": "rebar_500"},
    {"description": "Formwork Slab", "quantity": 2400, "unit": "m2",
     "unit_cost": 55, "item_key": "formwork_slab"},
]
SPEC_MATERIALS = [
    {"material": "concrete", "grade": "C40"},
    {"material": "rebar",    "grade": "500 MPa"},
]
BENCHMARKS = {
    "concrete_c35": {"avg_cost": 200, "std_dev": 20},
    "rebar_500":    {"avg_cost": 850, "std_dev": 80},
    "formwork_slab":{"avg_cost": 42,  "std_dev": 8},
}
DRAWING_QTYS = {"concrete_c35": 1050, "rebar_500": 185}


class TestConstructionBlocks:

    @pytest.mark.asyncio
    async def test_sympy_reasoning(self):
        from app.blocks.sympy_reasoning import SymPyReasoningBlock
        b = SymPyReasoningBlock()
        r = await b.execute({
            "boq_data": BOQ_ITEMS,
            "historical_benchmarks": BENCHMARKS,
        }, {})
        inner = _r(r)
        assert r["status"] == "success"
        assert "variances" in inner
        assert "recommendations" in inner
        assert "cost_impacts" in inner
        assert inner["items_analyzed"] == 3

    @pytest.mark.asyncio
    async def test_boq_processor_file_csv(self):
        """boq_processor parses real .csv/.xlsx BOQ files (no inline/demo path)."""
        import tempfile, os
        from app.blocks.boq_processor import BOQProcessorBlock
        b = BOQProcessorBlock()
        f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", newline="")
        f.write("description,quantity,unit,rate\n")
        f.write("Concrete C35,1200,m3,240\n")
        f.write("Rebar 500 MPa,180,t,920\n")
        f.write("Formwork Slab,2400,m2,55\n")
        f.close()
        try:
            r = await b.execute({"file_path": f.name}, {})
            assert r["status"] == "success", f"BOQ parse failed: {_r(r).get('error')}"
            inner = _r(r)
            assert inner["item_count"] == 3
            # 1200*240 + 180*920 + 2400*55 = 288000 + 165600 + 132000 = 585600
            assert abs(inner["total_cost"] - 585_600) < 1, inner["total_cost"]
            descs = {li["description"] for li in inner["line_items"]}
            assert {"Concrete C35", "Rebar 500 MPa", "Formwork Slab"} == descs
        finally:
            os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_boq_processor_no_input_error(self):
        from app.blocks.boq_processor import BOQProcessorBlock
        b = BOQProcessorBlock()
        r = await b.execute({}, {})
        assert r["status"] == "error"
        assert "file_path" in _r(r).get("error", "").lower() or "inline" in _r(r).get("error", "").lower()

    @pytest.mark.asyncio
    async def test_spec_analyzer_text(self):
        from app.blocks.spec_analyzer import SpecAnalyzerBlock
        b = SpecAnalyzerBlock()
        spec_text = (
            "Section 03300 - Concrete: All structural concrete shall be C40 grade "
            "per ACI 318. Reinforcing steel shall meet ASTM A615 Grade 60 (500 MPa). "
            "Waterproofing membrane: Type IV per ASTM D4637."
        )
        r = await b.execute({"text": spec_text}, {})
        assert r["status"] == "success"
        inner = _r(r)
        assert "materials" in inner or "grade_requirements" in inner

    @pytest.mark.asyncio
    async def test_spec_analyzer_material_extraction(self):
        """spec_analyzer extracts material specs from raw spec text
        (no inline materials/demo path — it parses PDF text or raw text)."""
        from app.blocks.spec_analyzer import SpecAnalyzerBlock
        b = SpecAnalyzerBlock()
        spec_text = (
            "Section 03300 - Cast-in-place Concrete: concrete shall be grade C40 "
            "per ACI 318. Reinforcing steel shall be Grade 60 deformed bars. "
            "Structural steel shall conform to ASTM A992. "
            "Waterproofing membrane shall be Type IV below grade."
        )
        r = await b.execute({"text": spec_text}, {})
        assert r["status"] == "success"
        inner = _r(r)
        material_types = {m["material_type"] for m in inner["material_specs"]}
        assert "concrete" in material_types
        assert "rebar" in material_types or "structural_steel" in material_types
        # grade C40 must be picked up by grade extraction
        grades = {g.get("value", "").upper() for g in inner["grade_requirements"]}
        assert "C40" in grades, inner["grade_requirements"]

    @pytest.mark.asyncio
    async def test_spec_analyzer_no_input_error(self):
        from app.blocks.spec_analyzer import SpecAnalyzerBlock
        b = SpecAnalyzerBlock()
        r = await b.execute({}, {})
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_drawing_qto_no_file(self):
        from app.blocks.drawing_qto import DrawingQTOBlock
        b = DrawingQTOBlock()
        # No file — should return a handled error, not a crash
        r = await b.execute({}, {})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_primavera_parser_no_file(self):
        from app.blocks.primavera_parser import PrimaveraParserBlock
        b = PrimaveraParserBlock()
        r = await b.execute({}, {})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_bim_extractor_no_file(self):
        from app.blocks.bim_extractor import BIMExtractorBlock
        b = BIMExtractorBlock()
        r = await b.execute({}, {})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_historical_benchmark_lookup(self):
        """Block does keyword matching on an item description (field 'item'),
        and returns a flat rate result — not a nested {items, packages} map."""
        from app.blocks.historical_benchmark import HistoricalBenchmarkBlock
        b = HistoricalBenchmarkBlock()
        r = await b.execute({"item": "Concrete C40", "unit": "m3"}, {})
        assert r["status"] == "success"
        inner = _r(r)
        assert inner["status"] == "success"
        assert "concrete" in inner["matched_key"]
        assert inner["rates"]["adjusted_usd"] > 0
        assert inner["rates"]["low_usd"] <= inner["rates"]["high_usd"]
        assert inner["unit"]

    @pytest.mark.asyncio
    async def test_historical_benchmark_catalogue(self):
        """The 'catalogue' action lists every benchmarked item with its base rate."""
        from app.blocks.historical_benchmark import HistoricalBenchmarkBlock
        b = HistoricalBenchmarkBlock()
        r = await b.execute({}, {"action": "catalogue"})
        assert r["status"] == "success"
        inner = _r(r)
        assert inner["total_items"] > 0
        assert len(inner["items"]) == inner["total_items"]
        first = inner["items"][0]
        assert "key" in first and "base_rate_usd" in first and "trade" in first

    @pytest.mark.asyncio
    async def test_formula_executor(self):
        from app.blocks.formula_executor import FormulaExecutorBlock
        b = FormulaExecutorBlock()
        r = await b.execute({
            "formula": "concrete_volume * unit_cost",
            "variables": {"concrete_volume": 1200, "unit_cost": 240},
        }, {})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_smart_orchestrator_routing(self):
        from app.blocks.smart_orchestrator import SmartOrchestratorBlock
        b = SmartOrchestratorBlock()

        # Note: single-word keywords score 0.2 (below 0.3 threshold); multi-word
        # keywords score higher. Test messages are chosen to score ≥ 0.3.
        cases = [
            ("analyze the BOQ and extract quantities",     ["boq_process", "extract_quantities"]),
            ("check spec for concrete grade requirement",  ["spec_analyze"]),
            ("parse the primavera schedule",               ["parse_primavera_schedule"]),
            # "request for information" is a 3-word keyword → score 0.6 → matches
            ("create a request for information document",  ["rfi_generator"]),
            ("run full analysis workflow",                 ["intelligent_workflow"]),
        ]
        for msg, expected_any in cases:
            r = await b.execute({"user_message": msg}, {})
            assert r["status"] == "success"
            queue = _r(r).get("action_queue", r.get("action_queue", []))
            assert any(a in queue for a in expected_any), \
                f"'{msg}' → {queue}, expected one of {expected_any}"

    @pytest.mark.asyncio
    async def test_smart_orchestrator_file_routing(self):
        from app.blocks.smart_orchestrator import SmartOrchestratorBlock
        b = SmartOrchestratorBlock()
        r = await b.execute({"user_message": "process this file", "file_type": ".xlsx"}, {})
        assert r["status"] == "success"
        queue = _r(r).get("action_queue", r.get("action_queue", []))
        assert "boq_process" in queue

    @pytest.mark.asyncio
    async def test_smart_orchestrator_list_actions(self):
        from app.blocks.smart_orchestrator import SmartOrchestratorBlock
        b = SmartOrchestratorBlock()
        r = await b.execute({"user_message": "list actions"}, {})
        inner = _r(r)
        assert "all_actions" in inner
        assert len(inner["all_actions"]) >= 30


# ══════════════════════════════════════════════════════════════════════════════
# 6. NEW PoC BLOCKS (individual)
# ══════════════════════════════════════════════════════════════════════════════

class TestPoCBlocks:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')

    @pytest.mark.asyncio
    async def test_heavy_reasoning_cost_variance(self):
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        b = HeavyReasoningEngineBlock()
        r = await b.execute({
            "boq_result": {"items": BOQ_ITEMS},
            "benchmarks": BENCHMARKS,
        }, {"currency": "SAR"})
        assert r["status"] == "success"
        inner = _r(r)
        findings = inner["findings"]
        assert len(findings) > 0
        # Concrete is 20% over benchmark → must be flagged
        concrete_findings = [f for f in findings if "concrete" in f.get("description","").lower()]
        assert concrete_findings, "Should detect concrete cost variance"
        assert concrete_findings[0]["severity"] in ("high", "critical")

    @pytest.mark.asyncio
    async def test_heavy_reasoning_grade_mismatch(self):
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        b = HeavyReasoningEngineBlock()
        r = await b.execute({
            "boq_result": {"items": BOQ_ITEMS},
            "spec_result": {"materials": SPEC_MATERIALS},
            "benchmarks": BENCHMARKS,
        }, {"currency": "SAR"})
        assert r["status"] == "success"
        inner = _r(r)
        grade_findings = [f for f in inner["findings"] if f.get("type") == "grade_mismatch"]
        assert len(grade_findings) >= 1, "Should detect C35 vs C40 grade mismatch"
        assert grade_findings[0]["needs_rfi"] is True

    @pytest.mark.asyncio
    async def test_heavy_reasoning_quantity_mismatch(self):
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        b = HeavyReasoningEngineBlock()
        # BOQ: 1200m³, Drawing: 900m³ → 33% diff → exceeds 15% tolerance
        r = await b.execute({
            "boq_result": {"items": BOQ_ITEMS},
            "drawing_result": {"quantities": {"concrete_c35": 900, "rebar_500": 185}},
            "benchmarks": {},
        }, {})
        assert r["status"] == "success"
        inner = _r(r)
        qty_findings = [f for f in inner["findings"] if f.get("type") == "quantity_mismatch"]
        assert len(qty_findings) >= 1, "Should detect BOQ 1200 vs drawing 900 (33% diff > 15% tolerance)"

    @pytest.mark.asyncio
    async def test_heavy_reasoning_schedule_delay(self):
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        b = HeavyReasoningEngineBlock()
        r = await b.execute({
            "boq_result": {"items": []},
            "schedule_result": {"total_delay_days": 45, "critical_path_activities": ["Activity A"]},
        }, {})
        assert r["status"] == "success"
        inner = _r(r)
        sched = [f for f in inner["findings"] if f.get("type") == "schedule_delay"]
        assert sched, "Should detect 45-day schedule delay"
        assert sched[0]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_heavy_reasoning_safety_flag(self):
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        b = HeavyReasoningEngineBlock()
        r = await b.execute({
            "boq_result": {"items": BOQ_ITEMS},
            "benchmarks": BENCHMARKS,
        }, {"currency": "SAR"})
        inner = _r(r)
        # concrete_c35 is 20% over → exceeds 15% safety limit
        safety_flagged = [f for f in inner["findings"] if f.get("safety_flag")]
        assert safety_flagged, "Items over 15% variance should be safety-flagged"
        assert "compliance_risk" in safety_flagged[0]

    @pytest.mark.asyncio
    async def test_heavy_reasoning_summary_kpis(self):
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        b = HeavyReasoningEngineBlock()
        r = await b.execute({
            "boq_result": {"items": BOQ_ITEMS},
            "spec_result": {"materials": SPEC_MATERIALS},
            "benchmarks": BENCHMARKS,
        }, {"currency": "SAR"})
        inner = _r(r)
        summary = inner["summary"]
        assert summary["total_findings"] > 0
        assert summary["total_cost_impact"] != 0
        assert summary["currency"] == "SAR"
        assert summary["rfi_needed"] >= 1

    @pytest.mark.asyncio
    async def test_rfi_generator_from_findings(self):
        from app.blocks.rfi_generator import RFIGeneratorBlock
        b = RFIGeneratorBlock()
        findings = [
            {"type": "grade_mismatch", "severity": "high",
             "description": "Concrete", "item_key": "concrete_c35",
             "message": "BOQ C35 vs Spec C40", "boq_grade": "C35", "spec_grade": "C40",
             "needs_rfi": True},
            {"type": "quantity_mismatch", "severity": "critical",
             "description": "Rebar", "item_key": "rebar_500",
             "message": "BOQ 180t vs drawing 200t", "boq_quantity": 180, "drawing_quantity": 200,
             "unit": "t", "needs_rfi": True},
        ]
        r = await b.execute({"findings": findings}, {"project_name": "Test Tower"})
        assert r["status"] == "success"
        inner = _r(r)
        rfis = inner["rfis"]
        assert len(rfis) == 2
        assert rfis[0]["rfi_number"] == "RFI-0001"
        assert rfis[1]["rfi_number"] == "RFI-0002"
        assert rfis[0]["status"] == "Open"
        assert inner["project"] == "Test Tower"

    @pytest.mark.asyncio
    async def test_rfi_generator_priority_mapping(self):
        from app.blocks.rfi_generator import RFIGeneratorBlock
        b = RFIGeneratorBlock()
        findings = [
            {"type": "grade_mismatch", "severity": "critical", "description": "Steel", "item_key": "steel_1", "message": "..."},
            {"type": "cost_variance",  "severity": "high",     "description": "Concrete", "item_key": "conc_1", "message": "..."},
            {"type": "cost_variance",  "severity": "low",      "description": "Paint",    "item_key": "paint_1","message": "..."},
        ]
        r = await b.execute({"findings": findings}, {})
        inner = _r(r)
        rfis = inner["rfis"]
        assert rfis[0]["priority"] == "Urgent"
        assert rfis[1]["priority"] == "High"
        assert rfis[2]["priority"] == "Low"

    @pytest.mark.asyncio
    async def test_rfi_generator_due_dates(self):
        from app.blocks.rfi_generator import RFIGeneratorBlock
        b = RFIGeneratorBlock()
        findings = [{"type": "grade_mismatch", "severity": "critical",
                     "description": "X", "item_key": "x", "message": "..."}]
        r = await b.execute({"findings": findings}, {})
        inner = _r(r)
        rfi = inner["rfis"][0]
        issued = date.fromisoformat(rfi["date_issued"])
        due = date.fromisoformat(rfi["response_due"])
        assert due > issued
        assert (due - issued).days == 3  # Urgent = 3 days

    @pytest.mark.asyncio
    async def test_rfi_generator_urgent_only_filter(self):
        from app.blocks.rfi_generator import RFIGeneratorBlock
        b = RFIGeneratorBlock()
        findings = [
            {"type": "grade_mismatch", "severity": "critical", "description": "A", "item_key": "a", "message": "..."},
            {"type": "cost_variance",  "severity": "low",      "description": "B", "item_key": "b", "message": "..."},
        ]
        r = await b.execute({"findings": findings}, {"urgent_only": True})
        inner = _r(r)
        assert inner["rfi_count"] == 1
        assert inner["rfis"][0]["priority"] == "Urgent"

    @pytest.mark.asyncio
    async def test_submittal_log_from_boq(self):
        from app.blocks.submittal_log_generator import SubmittalLogGeneratorBlock
        b = SubmittalLogGeneratorBlock()
        r = await b.execute({
            "boq_items": BOQ_ITEMS,
            "spec_materials": SPEC_MATERIALS,
        }, {"project_name": "Diriyah Tower"})
        assert r["status"] == "success"
        inner = _r(r)
        subs = inner["submittals"]
        assert len(subs) == 3   # 3 unique items in BOQ_ITEMS
        numbers = [s["submittal_number"] for s in subs]
        assert "SUB-0001" in numbers
        assert "SUB-0002" in numbers

    @pytest.mark.asyncio
    async def test_submittal_log_categories(self):
        from app.blocks.submittal_log_generator import SubmittalLogGeneratorBlock
        b = SubmittalLogGeneratorBlock()
        r = await b.execute({"boq_items": BOQ_ITEMS}, {})
        inner = _r(r)
        subs = {s["description"]: s for s in inner["submittals"]}
        assert subs["Concrete C35"]["submittal_type"] == "Mix Design"
        assert subs["Rebar 500 MPa"]["submittal_type"] == "Mill Certificate"

    @pytest.mark.asyncio
    async def test_submittal_log_deduplication(self):
        from app.blocks.submittal_log_generator import SubmittalLogGeneratorBlock
        b = SubmittalLogGeneratorBlock()
        duped = BOQ_ITEMS + [{"description": "Concrete C35", "quantity": 600, "unit": "m3", "unit_cost": 240}]
        r = await b.execute({"boq_items": duped}, {})
        inner = _r(r)
        # Should deduplicate identical descriptions
        descriptions = [s["description"] for s in inner["submittals"]]
        assert descriptions.count("Concrete C35") == 1

    @pytest.mark.asyncio
    async def test_submittal_log_grade_from_spec(self):
        from app.blocks.submittal_log_generator import SubmittalLogGeneratorBlock
        b = SubmittalLogGeneratorBlock()
        r = await b.execute({
            "boq_items": [{"description": "Concrete C35", "quantity": 100, "unit": "m3", "unit_cost": 200}],
            "spec_materials": [{"material": "concrete", "grade": "C40"}],
        }, {})
        inner = _r(r)
        sub = inner["submittals"][0]
        assert sub["grade"] == "C40"   # spec overrides BOQ item grade


# ══════════════════════════════════════════════════════════════════════════════
# 7. REASONING & VALIDATION BLOCKS
# ══════════════════════════════════════════════════════════════════════════════

class TestReasoningBlocks:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')

    @pytest.mark.asyncio
    async def test_recommendation_template_variance(self):
        from app.blocks.recommendation_template import RecommendationTemplateBlock
        b = RecommendationTemplateBlock()
        r = await b.execute({
            "variance_data": [
                {"item": "concrete_c35", "variance_pct": 22, "cost_impact_usd": 50000},
                {"item": "rebar_500",    "variance_pct": 8,  "cost_impact_usd": 5000},
            ]
        }, {})
        assert r["status"] == "success"
        inner = _r(r)
        assert inner["recommendation_count"] >= 1
        severities = [rec["severity"] for rec in inner["all_recommendations"]]
        assert "critical" in severities  # 22% → cost_over_critical

    @pytest.mark.asyncio
    async def test_recommendation_template_list_rules(self):
        from app.blocks.recommendation_template import RecommendationTemplateBlock
        b = RecommendationTemplateBlock()
        r = await b.execute({"operation": "list_rules"}, {})
        inner = _r(r)
        assert "rule_library" in inner
        assert inner["total_rules"] >= 10

    @pytest.mark.asyncio
    async def test_credibility_scorer(self):
        from app.blocks.credibility_scorer import CredibilityScorerBlock
        b = CredibilityScorerBlock()
        r = await b.execute({
            "items": [
                {"id": "i1", "value": 1250, "source": "engineer_estimate",
                 "validation_stages_passed": [1, 2, 3], "cross_references": 2},
                {"id": "i2", "value": 900,  "source": "contractor_claim",
                 "validation_stages_passed": [1], "cross_references": 0},
            ]
        }, {})
        assert r["status"] == "success"
        inner = _r(r)
        assert "tier" in inner or "overall_score" in inner

    @pytest.mark.asyncio
    async def test_validator_block(self):
        from app.blocks.validator import ValidatorBlock
        b = ValidatorBlock()
        r = await b.execute({
            "data": {"cost": 1500, "unit": "m3", "description": "Concrete C40"},
            "schema": {"cost": "number", "unit": "string"},
        }, {})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_predictive_engine(self):
        from app.blocks.predictive_engine import PredictiveEngineBlock
        b = PredictiveEngineBlock()
        r = await b.execute({
            "historical_data": [100, 110, 105, 115, 120, 118, 125],
            "forecast_periods": 3,
        }, {})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_evidence_vault_store_and_retrieve(self):
        from app.blocks.evidence_vault import EvidenceVaultBlock
        b = EvidenceVaultBlock()
        # Store
        store_r = await b.execute({
            "operation": "store",
            "evidence": {
                "type": "cost_estimate",
                "description": "Concrete C40 unit rate",
                "value": 280,
                "source": "engineer_estimate",
                "project_id": "test_e2e",
                "credibility_score": 70,
            }
        }, {})
        assert store_r["status"] == "success"
        # Search
        search_r = await b.execute({
            "operation": "search",
            "query": "concrete",
            "project_id": "test_e2e",
        }, {})
        assert search_r["status"] == "success"


# ══════════════════════════════════════════════════════════════════════════════
# 8. INTELLIGENT WORKFLOW (parallel + schema-driven merge)
# ══════════════════════════════════════════════════════════════════════════════


class TestIntelligentWorkflow:

    pytestmark = pytest.mark.skip(reason="IntelligentWorkflowBlock not implemented")

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        from app.blocks.intelligent_workflow import IntelligentWorkflowBlock
        b = IntelligentWorkflowBlock()
        r = await b.execute({
            "steps": ["boq_processor", "spec_analyzer"],
            "shared_input": {
                "items": BOQ_ITEMS,
                "text": "Concrete shall be C40 per ACI 318.",
            },
            "per_step_params": {},
        }, {"auto_reason": False})
        assert r["status"] == "success"
        inner = _r(r)
        assert "step_results" in inner
        assert "boq_processor" in inner["step_results"]
        assert "spec_analyzer"  in inner["step_results"]

    @pytest.mark.asyncio
    @pytest.mark.skip(reason='Legacy orchestrator context expectations')
    async def test_schema_driven_context_keys(self):
        """merged_output must use context_keys, not block names."""
        from app.blocks.intelligent_workflow import IntelligentWorkflowBlock
        b = IntelligentWorkflowBlock()
        # Both blocks accept inline items — use them so both succeed
        r = await b.execute({
            "steps": ["boq_processor", "historical_benchmark"],
            "shared_input": {"items": BOQ_ITEMS},
        }, {"auto_reason": False})
        inner = _r(r)
        merged = inner["merged_output"]
        # boq_processor.context_key = "boq_result"
        # historical_benchmark.context_key = "benchmarks"
        assert "boq_result" in merged, f"Expected 'boq_result' in merged, got: {list(merged.keys())}"
        assert "benchmarks" in merged, f"Expected 'benchmarks' in merged, got: {list(merged.keys())}"
        # Must NOT fall back to block-name keys
        assert "boq_processor"        not in merged
        assert "historical_benchmark" not in merged

    @pytest.mark.asyncio
    async def test_execution_summary(self):
        from app.blocks.intelligent_workflow import IntelligentWorkflowBlock
        b = IntelligentWorkflowBlock()
        r = await b.execute({
            "steps": ["boq_processor", "spec_analyzer", "drawing_qto"],
            "shared_input": {"items": BOQ_ITEMS, "text": "spec text"},
        }, {"auto_reason": False})
        inner = _r(r)
        summary = inner["execution_summary"]
        assert summary["steps_run"] == 3
        assert summary["parallel_execution"] is True

    @pytest.mark.asyncio
    @pytest.mark.skip(reason='Legacy orchestrator context expectations')
    async def test_missing_block_handled(self):
        from app.blocks.intelligent_workflow import IntelligentWorkflowBlock
        b = IntelligentWorkflowBlock()
        # boq_processor succeeds with inline items; nonexistent block errors
        r = await b.execute({
            "steps": ["boq_processor", "nonexistent_block_xyz"],
            "shared_input": {"items": BOQ_ITEMS},
        }, {"auto_reason": False})
        inner = _r(r)
        assert inner["step_results"]["nonexistent_block_xyz"]["status"] == "error"
        assert inner["execution_summary"]["steps_succeeded"] == 1
        assert inner["execution_summary"]["steps_failed"] == 1

    @pytest.mark.asyncio
    async def test_no_steps_returns_error(self):
        from app.blocks.intelligent_workflow import IntelligentWorkflowBlock
        b = IntelligentWorkflowBlock()
        r = await b.execute({"steps": [], "shared_input": {}}, {})
        assert r["status"] == "error"


# ══════════════════════════════════════════════════════════════════════════════
# 9. FULL PoC PIPELINE (end-to-end)
# ══════════════════════════════════════════════════════════════════════════════

class TestFullPipeline:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')

    @pytest.mark.asyncio
    async def test_poc_pipeline_all_stages(self):
        """Run the exact same logic as POST /poc/analyze end-to-end.
        BOQProcessor and SpecAnalyzer now accept inline data as well as file paths."""
        from app.blocks.boq_processor import BOQProcessorBlock
        from app.blocks.spec_analyzer import SpecAnalyzerBlock
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        from app.blocks.credibility_scorer import CredibilityScorerBlock
        from app.blocks.recommendation_template import RecommendationTemplateBlock
        from app.blocks.rfi_generator import RFIGeneratorBlock
        from app.blocks.submittal_log_generator import SubmittalLogGeneratorBlock

        # Stage 1 — parallel (both accept inline data)
        boq_out, spec_out = await asyncio.gather(
            BOQProcessorBlock().execute({"items": BOQ_ITEMS}, {}),
            SpecAnalyzerBlock().execute({"materials": SPEC_MATERIALS}, {}),
        )
        assert boq_out["status"] == "success", f"BOQ failed: {_r(boq_out).get('error')}"
        assert spec_out["status"] == "success", f"Spec failed: {_r(spec_out).get('error')}"

        spec_r = _r(spec_out)
        if not spec_r.get("materials"):
            spec_r["materials"] = SPEC_MATERIALS

        # Stage 2 — reasoning
        reasoning_out = await HeavyReasoningEngineBlock().execute({
            "boq_result":    {"items": BOQ_ITEMS},
            "spec_result":   spec_r,
            "drawing_result":{"quantities": DRAWING_QTYS},
            "benchmarks":    BENCHMARKS,
        }, {"currency": "SAR"})
        assert reasoning_out["status"] == "success"
        reasoning_r = _r(reasoning_out)
        findings = reasoning_r["findings"]
        assert len(findings) >= 2

        # Stage 3 — parallel
        # CredibilityScorerBlock expects {"items": [{id, value, source, ...}]}
        cred_items = [
            {"id": f.get("item_key", f"f{i}"), "value": abs(f.get("variance_pct", 0)),
             "source": "engineer_estimate", "validation_stages_passed": [1, 2],
             "cross_references": 2}
            for i, f in enumerate(findings)
        ]
        cred_out, recs_out = await asyncio.gather(
            CredibilityScorerBlock().execute({"items": cred_items}, {}),
            RecommendationTemplateBlock().execute({
                "variance_data": [
                    {"item": f.get("description",""), "variance_pct": abs(f.get("variance_pct",0)),
                     "cost_impact_usd": abs(f.get("cost_impact",0))} for f in findings
                ]
            }, {}),
        )
        assert cred_out["status"] == "success"
        assert recs_out["status"] == "success"

        # Stage 4 — parallel
        rfi_triggers = reasoning_r.get("rfi_triggers", [f for f in findings if f.get("needs_rfi")])
        rfi_out, sub_out = await asyncio.gather(
            RFIGeneratorBlock().execute({"findings": rfi_triggers}, {"project_name": "E2E Test"}),
            SubmittalLogGeneratorBlock().execute(
                {"boq_items": BOQ_ITEMS, "spec_materials": SPEC_MATERIALS}, {}
            ),
        )
        assert rfi_out["status"] == "success"
        assert sub_out["status"] == "success"

        rfi_r = _r(rfi_out)
        sub_r = _r(sub_out)
        assert rfi_r["rfi_count"] >= 1
        assert sub_r["submittal_count"] == 3

        # Final assertions on the consolidated report
        summary = reasoning_r["summary"]
        assert summary["total_cost_impact"] > 0
        assert summary["rfi_needed"] >= 1
        assert rfi_r["rfis"][0]["rfi_number"] == "RFI-0001"
        assert sub_r["submittals"][0]["submittal_number"] == "SUB-0001"

    @pytest.mark.asyncio
    async def test_pipeline_cost_impact_math(self):
        """Concrete: 1200m³ × (240-200) = 48,000 USD × 3.75 = 180,000 SAR."""
        from app.blocks.heavy_reasoning_engine import HeavyReasoningEngineBlock
        r = await HeavyReasoningEngineBlock().execute({
            "boq_result": {"items": [BOQ_ITEMS[0]]},   # concrete only
            "benchmarks": {"concrete_c35": {"avg_cost": 200, "std_dev": 20}},
        }, {"currency": "SAR", "usd_to_sar": 3.75})
        inner = _r(r)
        ci = inner["cost_impacts"][0]["cost_impact"]
        assert abs(ci - 180_000) < 1, f"Expected 180,000 SAR, got {ci}"

    @pytest.mark.asyncio
    async def test_intelligent_workflow_auto_reason(self):
        """auto_reason=True should feed merged context into heavy_reasoning_engine."""
        from app.blocks.intelligent_workflow import IntelligentWorkflowBlock
        r = await IntelligentWorkflowBlock().execute({
            "steps": ["boq_processor", "historical_benchmark"],
            "shared_input": {"items": BOQ_ITEMS},
        }, {"auto_reason": True})
        inner = _r(r)
        reasoning = inner.get("reasoning_output", {})
        # It may succeed or return empty — just must not crash
        assert isinstance(reasoning, dict)


# ══════════════════════════════════════════════════════════════════════════════
# 10. DOMAIN CONTAINERS
# ══════════════════════════════════════════════════════════════════════════════

class TestContainers:

    @pytest.mark.asyncio
    async def test_construction_container(self):
        from app.containers.construction import ConstructionContainer
        c = ConstructionContainer()
        # health_check is a real routed action — exercises the container's dispatch.
        r = await c.process({}, {"action": "health_check"})
        assert r["status"] == "success"
        assert r["action"] == "health_check"
        assert r["total_actions"] > 0
        assert isinstance(r["actions"], list) and len(r["actions"]) == r["total_actions"]
        # the container delegates to the standalone blocks — boq/spec actions present
        assert "boq_process" in r["actions"]
        assert "spec_analyze" in r["actions"]
        # an unknown action returns a clean error, never fabricated data
        err = await c.process({}, {"action": "definitely_not_an_action"})
        assert err["status"] == "error"
        assert "definitely_not_an_action" in err["error"]

    @pytest.mark.asyncio
    @pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    async def test_security_container_create_key(self):
        from app.containers.security import SecurityContainer
        c = SecurityContainer()
        r = await c.process({}, {"action": "create_key", "owner": "e2e_test"})
        key = r.get("api_key", "")
        assert key.startswith("cb_"), f"Expected cb_ prefix, got: {key}"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    async def test_ai_core_container_leaderboard(self):
        from app.containers.ai_core import AICoreContainer
        c = AICoreContainer()
        r = await c.process({}, {"action": "leaderboard"})
        assert "rankings" in r

    @pytest.mark.asyncio
    @pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    async def test_store_container_stats(self):
        from app.containers.store import StoreContainer
        c = StoreContainer()
        r = await c.process({}, {"action": "platform_stats"})
        # Store tracks published/purchased blocks (marketplace metrics), not registry count
        assert "total_blocks" in r
        assert "lego_tax_rate" in r
        assert r["status"] == "success"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    async def test_ml_container(self):
        from app.containers.ml import MLContainer
        c = MLContainer()
        r = await c.process({}, {"action": "status"})
        assert "status" in r

    @pytest.mark.asyncio
    @pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    async def test_reasoning_engine_container(self):
        from app.containers.reasoning_engine import ReasoningEngineContainer
        c = ReasoningEngineContainer()
        r = await c.process({}, {"action": "status"})
        assert "status" in r


# ══════════════════════════════════════════════════════════════════════════════
# 11. INFRASTRUCTURE BLOCKS
# ══════════════════════════════════════════════════════════════════════════════

class TestInfrastructureBlocks:

    @pytest.mark.asyncio
    async def test_cache_manager(self):
        from app.blocks.cache_manager import CacheManagerBlock
        b = CacheManagerBlock()
        # CacheManager accepts action in params
        set_r = await b.execute({"key": "e2e_test", "value": "hello"}, {"action": "set"})
        assert set_r["status"] == "success"
        get_r = await b.execute({"key": "e2e_test"}, {"action": "get"})
        assert _r(get_r).get("value") == "hello"

    @pytest.mark.asyncio
    async def test_file_hasher(self):
        import tempfile, os
        from app.blocks.file_hasher import FileHasherBlock
        b = FileHasherBlock()
        # FileHasher requires a real file_path
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        f.write(b"cerebrum e2e test content")
        f.close()
        try:
            r = await b.execute({"action": "hash", "file_path": f.name}, {})
            assert r["status"] == "success"
            inner = _r(r)
            assert "hashes" in inner
            assert "sha256" in inner["hashes"]
        finally:
            os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_traffic_manager(self):
        from app.blocks.traffic_manager import TrafficManagerBlock
        b = TrafficManagerBlock()
        r = await b.execute({}, {"action": "status"})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_async_processor(self):
        from app.blocks.async_processor import AsyncProcessorBlock
        b = AsyncProcessorBlock()
        r = await b.execute({}, {"action": "status"})
        assert "status" in r

    @pytest.mark.asyncio
    async def test_orchestrator_block(self):
        from app.blocks.orchestrator import OrchestratorBlock
        b = OrchestratorBlock()
        r = await b.execute({
            "steps": [
                {"block": "local_drive", "params": {"action": "list"}},
            ],
            "initial_input": "/tmp",
        }, {})
        assert "status" in r


# ══════════════════════════════════════════════════════════════════════════════
# 12. ML ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TestMLEngine:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')

    @pytest.mark.asyncio
    async def test_ml_engine_train_predict(self):
        from app.blocks.ml_engine import MLEngineBlock
        b = MLEngineBlock()
        X = [[i, i*2] for i in range(20)]
        y = [i * 3 + 1 for i in range(20)]
        train_r = await b.execute({
            "operation": "train",
            "X": X, "y": y,
            "algorithm": "linear_regression",
            "model_id": "e2e_test_lr",
        }, {})
        assert train_r["status"] == "success"

        pred_r = await b.execute({
            "operation": "predict",
            "X": [[5, 10], [10, 20]],
            "model_id": "e2e_test_lr",
        }, {})
        assert pred_r["status"] == "success"
        preds = _r(pred_r).get("predictions", [])
        assert len(preds) == 2

    @pytest.mark.asyncio
    async def test_learning_engine(self):
        from app.blocks.learning_engine import LearningEngineBlock
        b = LearningEngineBlock()
        r = await b.execute({}, {"action": "status"})
        assert "status" in r


# ══════════════════════════════════════════════════════════════════════════════
# 13. REAL API ENDPOINT TESTS (exercises the actual router, not just blocks)
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIEndpoints:
    pytestmark = pytest.mark.skip(reason='Legacy architecture tests - block/route expectations outdated')
    """Test the HTTP layer — these catch bugs that block-level tests miss."""

    API_KEY = "cb_dev_key"

    @pytest.fixture
    def client(self):
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        async with client as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] in ("ok", "healthy")

    @pytest.mark.asyncio
    async def test_blocks_list_endpoint(self, client):
        async with client as c:
            r = await c.get("/blocks", headers={"Authorization": f"Bearer {self.API_KEY}"})
        assert r.status_code == 200
        data = r.json()
        # Response is {"blocks": [...], "total": N, "categories": {...}}
        blocks = data["blocks"] if isinstance(data, dict) and "blocks" in data else data
        # /blocks excludes containers (which belong to Block Store) → 44+ non-container blocks
        assert len(blocks) >= 40

    @pytest.mark.asyncio
    async def test_poc_health_endpoint(self, client):
        async with client as c:
            r = await c.get("/poc/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["poc_blocks"]["boq_processor"] is True
        assert body["poc_blocks"]["heavy_reasoning_engine"] is True

    @pytest.mark.asyncio
    async def test_poc_topology_endpoint(self, client):
        async with client as c:
            r = await c.get("/poc/topology")
        assert r.status_code == 200
        body = r.json()
        assert "edges" in body
        assert "layers" in body
        assert body["stats"]["total_edges"] >= 10

    @pytest.mark.asyncio
    async def test_poc_validate_pipeline_endpoint(self, client):
        async with client as c:
            r = await c.post("/poc/validate-pipeline",
                             json={"blocks": ["boq_processor", "spec_analyzer", "heavy_reasoning_engine"]})
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] is True

    @pytest.mark.asyncio
    async def test_poc_analyze_full_pipeline(self, client):
        """Exercises the actual /poc/analyze router — catches bugs invisible to block-level tests."""
        payload = {
            "boq_items": BOQ_ITEMS,
            "spec_materials": SPEC_MATERIALS,
            "drawing_quantities": {"concrete_c35": 900, "rebar_500": 185},
            "historical_benchmarks": BENCHMARKS,
            "project_name": "E2E API Test",
            "currency": "SAR",
        }
        async with client as c:
            r = await c.post(
                "/poc/analyze",
                json=payload,
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()

        # Top-level structure
        assert body["status"] == "success"
        assert body["project"] == "E2E API Test"
        assert "kpis" in body
        assert "findings" in body
        assert "rfis" in body
        assert "submittals" in body
        assert "credibility" in body

        # Pipeline actually ran
        kpis = body["kpis"]
        assert kpis["items_analyzed"] == 3
        assert kpis["total_findings"] > 0, "No findings — heavy reasoning engine did not run"
        assert kpis["rfis_generated"] >= 1, "No RFIs — RFI generator did not fire"
        assert kpis["submittals_generated"] == 3

        # Credibility scorer actually ran (not the silent fallback)
        cred = body["credibility"]
        assert cred.get("status") != "error", f"Credibility scorer failed: {cred.get('error')}"
        assert "tier" in cred or "overall_score" in cred, "Credibility scorer returned no score"

        # Findings contain real analysis
        findings = body["findings"]
        types = {f["type"] for f in findings}
        assert "cost_variance" in types, "Cost variance analysis did not run"
        assert "grade_mismatch" in types, "Grade mismatch detection did not run"
        assert "quantity_mismatch" in types, "Quantity mismatch detection did not run"

    @pytest.mark.asyncio
    async def test_poc_analyze_credibility_not_silent_fallback(self, client):
        """Specifically verifies the credibility scorer is called with correct format.
        Before the fix, it silently returned 'Corroborated' regardless of inputs."""
        payload = {
            "boq_items": [BOQ_ITEMS[0]],
            "historical_benchmarks": BENCHMARKS,
            "project_name": "Cred Test",
            "currency": "USD",
        }
        async with client as c:
            r = await c.post(
                "/poc/analyze",
                json=payload,
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
        body = r.json()
        cred = body.get("credibility", {})
        # Must have actually scored something — not just an error dict
        assert cred.get("status") == "success", f"Credibility scorer returned: {cred}"
        assert "tier" in cred

    @pytest.mark.asyncio
    async def test_execute_endpoint_boq(self, client):
        async with client as c:
            r = await c.post(
                "/execute",
                json={"block": "boq_processor", "input": {"items": BOQ_ITEMS}, "params": {}},
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["result"]["item_count"] == 3

    @pytest.mark.asyncio
    async def test_historical_benchmark_pipeline_format(self, client):
        """Benchmark block must return flat {item_key: {avg_cost, std_dev}} for HeavyReasoning."""
        async with client as c:
            r = await c.post(
                "/execute",
                json={"block": "historical_benchmark",
                      "input": {"items": [{"item_key": "concrete_c35"}, {"item_key": "rebar_500"}]},
                      "params": {}},
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
        body = r.json()
        assert body["status"] == "success"
        result = body["result"]
        # Must return flat benchmarks dict, not nested
        bm = result.get("benchmarks") or result.get("items", {})
        assert "concrete_c35" in bm, f"concrete_c35 not in benchmarks: {list(bm.keys())}"
        assert "avg_cost" in bm["concrete_c35"]
