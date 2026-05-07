"""Document Engine Block — LegoBlock adapter for UniversalAssembler discovery.

Drop-in block that exposes Parse → Reason → Map as a standard Cerebrum Block.
The assembler discovers this via blocks/document_engine/src/block.py.
"""
from typing import Dict, Any
from pathlib import Path

from blocks.base import LegoBlock

# Import engine logic from the block root package
from blocks.document_engine.reasoner import DocumentReasoner
from blocks.document_engine.mapper import DocumentMapper
from blocks.document_engine.main import parse_all


class DocumentEngineBlock(LegoBlock):
    """Document Reasoning Engine — 3-layer pipeline for technical document intelligence.

    Layer 1: Parse  (PDF/DOCX/XLSX → typed document objects)
    Layer 2: Reason (8 semantic pipelines over extracted content)
    Layer 3: Map    (structured YAML/JSON + downstream block feeds)
    """

    name = "document_engine"
    version = "1.0.0"
    requires = ["config"]
    layer = 6  # Domain layer (same as pdf, bim, ocr)
    tags = ["construction", "document", "intelligence", "domain"]
    default_config = {
        "config_path": "config.yaml",
        "extract_tables": True,
        "extract_glossary": True,
    }

    def __init__(self, hal_block=None, config: Dict[str, Any] = None):
        super().__init__(hal_block, config)
        self._config_loaded = False
        self._engine_config: Dict[str, Any] = {}

    async def initialize(self) -> bool:
        """Load config via dependency injection or fallback to file."""
        try:
            import yaml
            # 1. Try universal connector — config block injected by assembler
            config_block = self.get_dependency("config")
            if config_block and hasattr(config_block, "config"):
                self._engine_config = config_block.config.get("document_engine", {})
                if self._engine_config:
                    self._config_loaded = True
                    self.initialized = True
                    return True

            # 2. Fallback: load from file
            cfg_path = Path(__file__).parent.parent / self.config.get("config_path", "config.yaml")
            if cfg_path.exists():
                with open(cfg_path, "r") as f:
                    full = yaml.safe_load(f)
                self._engine_config = full.get("document_engine", full)
            self._config_loaded = True
            self.initialized = True
            return True
        except Exception as e:
            self.initialized = False
            return False

    async def execute(self, input_data: Dict) -> Dict:
        """Dispatch actions.

        Actions:
            analyze  → full Parse→Reason→Map pipeline (default)
            health   → block health metadata
        """
        action = input_data.get("action", "analyze")

        if action == "analyze":
            return await self._analyze(input_data)
        elif action == "health":
            return self.health()
        else:
            return {"error": f"Unknown action '{action}'", "available": ["analyze", "health"]}

    async def _analyze(self, data: Dict) -> Dict:
        """Run the 3-layer pipeline."""
        file_paths = {
            "pdf": data.get("pdf_path") or data.get("pdf"),
            "docx": data.get("docx_path") or data.get("docx"),
            "xlsx": data.get("xlsx_path") or data.get("xlsx"),
        }

        config = self._engine_config or self.config.get("document_engine", self.config)

        # Layer 1: Parse
        documents = parse_all(file_paths, config)

        # Layer 2: Reason
        reasoner = DocumentReasoner(config)
        reasoned = reasoner.reason(documents)

        # Layer 3: Map
        mapper = DocumentMapper(config)
        structured = mapper.map_to_structured(reasoned)

        result = structured.to_dict()
        result["_meta"] = {
            "documents_parsed": len(documents),
            "pipelines_run": 8,
        }
        return result

    def health(self) -> Dict[str, Any]:
        h = super().health()
        h.update({
            "config_loaded": self._config_loaded,
            "pipelines": [
                "glossary_extraction",
                "requirement_mapping",
                "constraint_extraction",
                "schedule_targets",
                "equipment_specs",
                "diagram_interpretation",
                "wbs_mapping",
                "risk_identification",
            ],
            "supports": ["pdf", "docx", "xlsx"],
        })
        return h
