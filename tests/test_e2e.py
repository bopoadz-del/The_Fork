"""
Cerebrum Blocks — End-to-End Test Suite
Covers: registry, every live block group, domain containers, API routing.
"""

import sys
import os
from typing import Dict

import pytest

from tests.conftest import listable_block_count, requires_construction_kit

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
    def test_no_import_errors(self):
        """Importing the full registry must not raise."""
        from app.blocks import BLOCK_REGISTRY, get_block, get_all_blocks
        assert isinstance(BLOCK_REGISTRY, dict) and BLOCK_REGISTRY
        assert callable(get_block)
        assert callable(get_all_blocks)

    @requires_construction_kit
    def test_boq_processor_in_registry(self):
        from app.blocks import BLOCK_REGISTRY, get_block
        assert get_block("boq_processor") is BLOCK_REGISTRY["boq_processor"]


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
        # "." lists the configured drive root, which always exists.
        r = await b.execute(".", {"operation": "list"})
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
BENCHMARKS = {
    "concrete_c35": {"avg_cost": 200, "std_dev": 20},
    "rebar_500":    {"avg_cost": 850, "std_dev": 80},
    "formwork_slab":{"avg_cost": 42,  "std_dev": 8},
}


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

    def test_historical_benchmark_block_is_removed(self):
        """historical_benchmark was deleted — it shipped a hardcoded 2024 RS-Means
        snapshot that would drift silently. The container's benchmark_lookup()
        now returns an honest no-data error; learning_engine will accumulate
        real rates from user-supplied data over time. This test guards against
        the block being re-introduced as canned data."""
        from app.blocks import BLOCK_REGISTRY
        assert "historical_benchmark" not in BLOCK_REGISTRY, (
            "historical_benchmark must stay deleted; if a real (not canned) "
            "rate source is added, name it differently and update this test."
        )
        # Importing the deleted module must fail.
        with pytest.raises(ImportError):
            import app.blocks.historical_benchmark  # noqa: F401

    @pytest.mark.asyncio
    async def test_container_benchmark_lookup_returns_honest_error(self):
        """ConstructionContainer.benchmark_lookup() keeps its method signature
        for forward-compat with a future learning-based benchmark, but for now
        always returns a structured error pointing callers at supplier quotes."""
        from app.containers.construction import ConstructionContainer
        c = ConstructionContainer()
        r = await c.benchmark_lookup({"item": "Concrete C40", "unit": "m3"}, {})
        assert r["status"] == "error"
        assert "historical benchmark" in r["error"].lower()

    @pytest.mark.asyncio
    async def test_formula_executor(self):
        from app.blocks.formula_executor_v2 import FormulaExecutorV2Block
        b = FormulaExecutorV2Block()
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
# 6. REASONING & RECOMMENDATION BLOCKS
# ══════════════════════════════════════════════════════════════════════════════

class TestReasoningBlocks:

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


# ══════════════════════════════════════════════════════════════════════════════
# 7. DOMAIN CONTAINERS
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


# ══════════════════════════════════════════════════════════════════════════════
# 8. INFRASTRUCTURE BLOCKS
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
# 9. LEARNING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TestLearningEngine:

    @pytest.mark.asyncio
    async def test_learning_engine(self):
        from app.blocks.learning_engine import LearningEngineBlock
        b = LearningEngineBlock()
        r = await b.execute({}, {"action": "status"})
        assert "status" in r


# ══════════════════════════════════════════════════════════════════════════════
# 10. REAL API ENDPOINT TESTS (exercises the actual router, not just blocks)
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIEndpoints:
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
        # /blocks excludes containers (which belong to Block Store)
        assert len(blocks) >= listable_block_count()

    @requires_construction_kit
    @pytest.mark.asyncio
    async def test_execute_endpoint_boq(self, client):
        """POST /execute drives boq_processor through the real HTTP layer (auth + routing).
        boq_processor parses a real .csv/.xlsx file — there is no inline-data path."""
        import tempfile, os
        f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", newline="")
        f.write("description,quantity,unit,rate\n")
        f.write("Concrete C35,1200,m3,240\n")
        f.write("Rebar 500 MPa,180,t,920\n")
        f.write("Formwork Slab,2400,m2,55\n")
        f.close()
        try:
            async with client as c:
                r = await c.post(
                    "/execute",
                    json={"block": "boq_processor",
                          "input": {"file_path": f.name}, "params": {}},
                    headers={"Authorization": f"Bearer {self.API_KEY}"},
                )
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "success", body
            assert body["result"]["item_count"] == 3
            assert abs(body["result"]["total_cost"] - 585_600) < 1
        finally:
            os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_execute_endpoint_rejects_removed_historical_benchmark(self, client):
        """historical_benchmark was deleted from BLOCK_REGISTRY. POST /execute
        targeting it must surface an error rather than silently succeeding —
        this test exists to catch accidental re-introduction of the block."""
        async with client as c:
            r = await c.post(
                "/execute",
                json={"block": "historical_benchmark",
                      "input": {"item": "Concrete C40", "unit": "m3"},
                      "params": {}},
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
        # Whatever the exact status, the response must not pretend this block
        # exists. Accept either an HTTP error or a JSON body with status="error".
        if r.status_code == 200:
            body = r.json()
            assert body.get("status") == "error" or body.get("result", {}).get("status") == "error", (
                f"Removed block silently returned success: {body}"
            )
        else:
            assert r.status_code in (400, 404, 422, 500), r.status_code

    @pytest.mark.asyncio
    async def test_execute_endpoint_requires_auth(self, client):
        """The /execute route is auth-protected — a missing key must be rejected.
        Uses `translate` because it's a stable, side-effect-free block; the
        auth guard fires before any block lookup."""
        async with client as c:
            r = await c.post(
                "/execute",
                json={"block": "translate",
                      "input": {"text": "hello"}, "params": {"target": "es"}},
            )
        assert r.status_code in (401, 403), r.status_code
