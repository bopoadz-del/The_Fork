"""Document Engine block for Cerebrum-Blocks.

Provides Parse → Reason → Map pipeline for technical document intelligence.
"""
from .main import main, parse_all
from .reasoner import DocumentReasoner, ReasonedOutput
from .mapper import DocumentMapper, StructuredDocument

# LegoBlock integration (optional — assembler will discover this if imported)
try:
    from blocks.base import LegoBlock

    class DocumentEngineBlock(LegoBlock):
        name = "document_engine"
        version = "1.0.0"
        requires = []
        layer = 2
        tags = ["construction", "document", "intelligence"]

        async def execute(self, input_data: dict) -> dict:
            file_paths = {
                "pdf": input_data.get("pdf_path"),
                "docx": input_data.get("docx_path"),
                "xlsx": input_data.get("xlsx_path"),
            }
            config = self.config.get("document_engine", self.config)
            documents = parse_all(file_paths, config)
            reasoner = DocumentReasoner(config)
            reasoned = reasoner.reason(documents)
            mapper = DocumentMapper(config)
            structured = mapper.map_to_structured(reasoned)
            return structured.to_dict()

        async def initialize(self) -> bool:
            self.initialized = True
            return True

except ImportError:
    DocumentEngineBlock = None  # type: ignore

__all__ = [
    "main",
    "parse_all",
    "DocumentReasoner",
    "ReasonedOutput",
    "DocumentMapper",
    "StructuredDocument",
    "DocumentEngineBlock",
]
