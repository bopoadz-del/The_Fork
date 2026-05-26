"""Platform Blocks — Construction Intelligence Platform.

Blocks are loaded RESILIENTLY: each block module is imported in isolation, and
one whose import fails (missing optional dependency, import-time error, syntax
error, ...) is logged and skipped. A single bad block can no longer crash the
whole registry — and therefore the whole app — at startup.

``BLOCK_REGISTRY`` contains only blocks that loaded; ``FAILED_BLOCKS`` maps the
name of any block that failed to a short error string for diagnostics.
"""

import importlib
import logging
from typing import Dict, List, Tuple

from app.core.universal_base import UniversalBlock, UniversalContainer
from app.core.typed_block import TypedBlock

logger = logging.getLogger(__name__)

# (registry name, module path, class name) — the single source of truth for
# which blocks exist. Loaded one at a time so a failure is isolated.
_BLOCK_SPECS: List[Tuple[str, str, str]] = [
    # Document Extraction
    ("pdf",                 "app.blocks.pdf",                  "PDFBlock"),
    ("pdf_v2",              "app.blocks.pdf_v2",               "PDFBlockV2"),
    ("ocr",                 "app.blocks.ocr",                  "OCRBlock"),
    ("ocr_v2",              "app.blocks.ocr_v2",               "OCRBlockV2"),
    ("image",               "app.blocks.image",                "ImageBlock"),
    ("document_engine",     "app.blocks.document_engine",      "DocumentEngineBlock"),
    # AI / Language
    ("chat",                "app.blocks.chat",                 "ChatBlock"),
    ("translate",           "app.blocks.translate",            "TranslateBlock"),
    ("voice",               "app.blocks.voice",                "VoiceBlock"),
    ("web",                 "app.blocks.web",                  "WebBlock"),
    # Construction Intelligence
    ("construction",        "app.containers",                  "ConstructionContainer"),
    ("construction_v2",     "app.blocks.construction_v2",      "ConstructionBlockV2"),
    ("boq_processor",       "app.blocks.boq_processor",        "BOQProcessorBlock"),
    ("bim",                 "app.blocks.bim",                  "BIMBlock"),
    ("bim_extractor",       "app.blocks.bim_extractor",        "BIMExtractorBlock"),
    ("drawing_qto",         "app.blocks.drawing_qto",          "DrawingQTOBlock"),
    ("primavera_parser",    "app.blocks.primavera_parser",     "PrimaveraParserBlock"),
    ("spec_analyzer",       "app.blocks.spec_analyzer",        "SpecAnalyzerBlock"),
    ("formula_executor",    "app.blocks.formula_executor",     "FormulaExecutorBlock"),
    ("formula_executor_v2", "app.blocks.formula_executor_v2",  "FormulaExecutorV2Block"),
    ("project_reasoner",    "app.blocks.project_reasoner",     "ProjectReasonerBlock"),
    ("sympy_reasoning",     "app.blocks.sympy_reasoning",      "SymPyReasoningBlock"),
    # historical_benchmark removed: it shipped 2024 RS-Means snapshots that
    # would drift silently. The container's _get_historical_benchmark_block()
    # already returns an "unavailable" error path. Real historical data will
    # be accumulated by the learning_engine block over time.
    ("smart_orchestrator",  "app.blocks.smart_orchestrator",   "SmartOrchestratorBlock"),
    ("orchestrator",        "app.blocks.orchestrator",         "OrchestratorBlock"),
    # File Access
    ("local_drive",         "app.blocks.local_drive",          "LocalDriveBlock"),
    ("google_drive",        "app.blocks.google_drive",         "GoogleDriveBlock"),
    ("onedrive",            "app.blocks.onedrive",             "OneDriveBlock"),
    # Search & Memory
    ("vector_search",       "app.blocks.vector_search",        "VectorSearchBlock"),
    ("zvec",                "app.blocks.zvec",                 "ZvecBlock"),
    ("cache_manager",       "app.blocks.cache_manager",        "CacheManagerBlock"),
    # MCP (agent interop)
    ("mcp_adapter",         "app.blocks.mcp_adapter",          "MCPAdapterBlock"),
    ("mcp_consumer",        "app.blocks.mcp_consumer",         "MCPConsumerBlock"),
    # Other
    ("code",                "app.blocks.code",                 "CodeBlock"),
    ("search",              "app.blocks.search",               "SearchBlock"),
    ("android_drive",       "app.blocks.android_drive",        "AndroidDriveBlock"),
]


def _load_blocks(
    specs: List[Tuple[str, str, str]]
) -> Tuple[Dict[str, type], Dict[str, str]]:
    """Import each ``(name, module, class)`` spec in isolation.

    Returns ``(registry, failed)``. A spec whose import raises is recorded in
    ``failed`` (name -> error string) and omitted from ``registry`` rather than
    aborting the whole load — one broken block cannot take down the app.
    """
    registry: Dict[str, type] = {}
    failed: Dict[str, str] = {}
    for name, module, class_name in specs:
        try:
            mod = importlib.import_module(module)
            registry[name] = getattr(mod, class_name)
        except Exception as exc:  # noqa: BLE001 — isolate any import-time failure
            failed[name] = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Block '%s' failed to load and was skipped: %s", name, exc
            )
    return registry, failed


BLOCK_REGISTRY, FAILED_BLOCKS = _load_blocks(_BLOCK_SPECS)

# Re-export each loaded class as a module attribute so existing
# `from app.blocks import XBlock` imports keep working.
for _cls in BLOCK_REGISTRY.values():
    globals()[_cls.__name__] = _cls

if FAILED_BLOCKS:
    logger.warning(
        "%d/%d blocks failed to load: %s",
        len(FAILED_BLOCKS), len(_BLOCK_SPECS), ", ".join(sorted(FAILED_BLOCKS)),
    )


def get_block(name: str):
    return BLOCK_REGISTRY.get(name)


def get_all_blocks():
    return BLOCK_REGISTRY


__all__ = [
    "UniversalBlock",
    "UniversalContainer",
    "TypedBlock",
    "BLOCK_REGISTRY",
    "FAILED_BLOCKS",
    "get_block",
    "get_all_blocks",
]
