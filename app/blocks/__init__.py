"""Platform Blocks — Construction Intelligence Platform."""

from app.core.universal_base import UniversalBlock, UniversalContainer
from app.core.typed_block import TypedBlock

# ── Document Extraction ──────────────────────────────────────────────────────
from .pdf import PDFBlock
from .pdf_v2 import PDFBlockV2
from .ocr import OCRBlock
from .ocr_v2 import OCRBlockV2
from .image import ImageBlock
from .document_engine import DocumentEngineBlock

# ── AI / Language ─────────────────────────────────────────────────────────────
from .chat import ChatBlock
from .translate import TranslateBlock
from .voice import VoiceBlock
from .web import WebBlock

# ── Construction Intelligence ─────────────────────────────────────────────────
from .boq_processor import BOQProcessorBlock
from .bim_extractor import BIMExtractorBlock
from .bim import BIMBlock
from .drawing_qto import DrawingQTOBlock
from .primavera_parser import PrimaveraParserBlock
from .spec_analyzer import SpecAnalyzerBlock
from .formula_executor import FormulaExecutorBlock
from .sympy_reasoning import SymPyReasoningBlock
from .historical_benchmark import HistoricalBenchmarkBlock
from .smart_orchestrator import SmartOrchestratorBlock
from .construction_v2 import ConstructionBlockV2

# ── File Access ───────────────────────────────────────────────────────────────
from .local_drive import LocalDriveBlock
from .google_drive import GoogleDriveBlock
from .onedrive import OneDriveBlock

# ── Search & Memory ───────────────────────────────────────────────────────────
from .vector_search import VectorSearchBlock
from .zvec import ZvecBlock
from .cache_manager import CacheManagerBlock
from .mcp_adapter import MCPAdapterBlock
from .mcp_consumer import MCPConsumerBlock
from .code import CodeBlock
from .search import SearchBlock
from .android_drive import AndroidDriveBlock

# ── Main Construction Container ───────────────────────────────────────────────
from app.containers import ConstructionContainer


BLOCK_REGISTRY = {
    # Document Extraction
    "pdf":              PDFBlock,
    "pdf_v2":           PDFBlockV2,
    "ocr":              OCRBlock,
    "ocr_v2":           OCRBlockV2,
    "image":            ImageBlock,
    "document_engine":  DocumentEngineBlock,

    # AI / Language
    "chat":             ChatBlock,
    "translate":        TranslateBlock,
    "voice":            VoiceBlock,
    "web":              WebBlock,

    # Construction Intelligence
    "construction":         ConstructionContainer,
    "construction_v2":      ConstructionBlockV2,
    "boq_processor":        BOQProcessorBlock,
    "bim":                  BIMBlock,
    "bim_extractor":        BIMExtractorBlock,
    "drawing_qto":          DrawingQTOBlock,
    "primavera_parser":     PrimaveraParserBlock,
    "spec_analyzer":        SpecAnalyzerBlock,
    "formula_executor":     FormulaExecutorBlock,
    "sympy_reasoning":      SymPyReasoningBlock,
    "historical_benchmark": HistoricalBenchmarkBlock,
    "smart_orchestrator":   SmartOrchestratorBlock,

    # File Access
    "local_drive":      LocalDriveBlock,
    "google_drive":     GoogleDriveBlock,
    "onedrive":         OneDriveBlock,

    # Search & Memory
    "vector_search":    VectorSearchBlock,
    "zvec":             ZvecBlock,
    "cache_manager":    CacheManagerBlock,

    # MCP (agent interop)
    "mcp_adapter":      MCPAdapterBlock,
    "mcp_consumer":     MCPConsumerBlock,

    # Other
    "code":             CodeBlock,
    "search":           SearchBlock,
    "android_drive":    AndroidDriveBlock,
}


def get_block(name: str):
    return BLOCK_REGISTRY.get(name)


def get_all_blocks():
    return BLOCK_REGISTRY


__all__ = [
    "UniversalBlock",
    "UniversalContainer",
    "TypedBlock",
    "BLOCK_REGISTRY",
    "get_block",
    "get_all_blocks",
]
