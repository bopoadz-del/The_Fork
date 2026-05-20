"""Document Engine Block — Platform wrapper for Parse → Reason → Map pipeline.

Exposes the document_engine block to the Cerebrum platform via:
  POST /execute { "block": "document_engine", "input": { "pdf_path": "..." } }

Integrates with platform blocks:
  - pdf  : PDF text extraction (PyMuPDF)
  - ocr  : Image / scanned PDF OCR fallback
"""

import os
from typing import Any, Dict, Optional
from app.core.universal_base import UniversalBlock


class DocumentEngineBlock(UniversalBlock):
    """Technical document reasoning engine.

    Ingests PDF / DOCX / XLSX, runs 8 semantic reasoning pipelines,
    and outputs structured YAML/JSON consumable by schedule_engine,
    cost_engine, and risk_engine downstream blocks.
    """

    name = "document_engine"
    version = "1.0.0"
    description = "Parse → Reason → Map pipeline for technical document intelligence"
    layer = 3
    tags = ["domain", "construction", "documents", "reasoning", "scheduling"]
    requires = ["pdf", "ocr"]

    default_config = {
        "extract_tables": True,
        "extract_glossary": True,
        "output_format": "yaml",
        "use_platform_pdf": True,
        "use_platform_ocr": True,
    }

    ui_schema = {
        "input": {
            "type": "files",
            "accept": [".pdf", ".docx", ".xlsx"],
            "placeholder": "Upload BOD, RFP, or spec documents...",
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "glossary", "type": "list", "label": "Glossary Terms"},
                {"name": "requirements", "type": "list", "label": "Requirements"},
                {"name": "constraints", "type": "list", "label": "Constraints"},
                {"name": "schedule_targets", "type": "list", "label": "Schedule Targets"},
                {"name": "equipment_specs", "type": "list", "label": "Equipment Specs"},
                {"name": "risks", "type": "list", "label": "Risks"},
                {"name": "downstream", "type": "object", "label": "Downstream Feed"},
            ],
        },
        "quick_actions": [
            {"icon": "📄", "label": "Analyze BOD", "prompt": "Extract glossary, constraints, and equipment lead times from Basis of Design"},
            {"icon": "📋", "label": "Analyze RFP", "prompt": "Extract requirements, schedule targets, and risks from RFP"},
            {"icon": "📊", "label": "Schedule Feed", "prompt": "Generate procurement activities and milestones for schedule_engine"},
            {"icon": "⚠️", "label": "Risk Register", "prompt": "Extract all risks and output risk_engine feed"},
        ],
    }

    async def _parse_with_platform_pdf(self, file_path: str) -> Optional[str]:
        """Use platform PDF block for text extraction (universal connector)."""
        pdf_block = self.get_dep("pdf")
        if pdf_block is None:
            return None
        result = await pdf_block.process({"file_path": file_path})
        if result.get("status") == "success":
            return result.get("text", "")
        return None

    async def _parse_with_platform_ocr(self, file_path: str) -> Optional[str]:
        """Use platform OCR block for image/scanned PDF fallback (universal connector).

        Side effect: captures the OCR markup/redline verdict (Roadmap V2 · Epic 5)
        on ``self._last_ocr_markup`` so `process()` can surface it — annotated
        regions are flagged for the user, not presented as clean extracted data.
        """
        ocr_block = self.get_dep("ocr")
        if ocr_block is None:
            return None
        result = await ocr_block.process({"file_path": file_path})
        if isinstance(result, dict) and result.get("markup"):
            self._last_ocr_markup = result.get("markup")
        if result.get("status") == "success":
            return result.get("text", "")
        return None

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """Main entry point — run the 3-layer pipeline."""
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        # Reset the per-call OCR markup verdict (Roadmap V2 · Epic 5). Populated
        # by `_parse_with_platform_ocr` when a scanned input carries redlines.
        self._last_ocr_markup = None

        # InputAdapter may wrap bare file path as {"text": "/path/to/file.pdf"} — detect by extension
        raw_path = data.get("text") or data.get("input") or (input_data if isinstance(input_data, str) else "")
        raw_ext = os.path.splitext(raw_path)[1].lower() if raw_path else ""

        file_paths = {
            "pdf": data.get("pdf_path") or data.get("pdf") or params.get("pdf_path") or (raw_path if raw_ext == ".pdf" else None),
            "docx": data.get("docx_path") or data.get("docx") or params.get("docx_path") or (raw_path if raw_ext in (".docx", ".doc") else None),
            "xlsx": data.get("xlsx_path") or data.get("xlsx") or params.get("xlsx_path") or (raw_path if raw_ext in (".xlsx", ".xls") else None),
        }

        if not any(file_paths.values()):
            return {"status": "error", "error": "No input files provided (pdf/docx/xlsx). Pass file_path as pdf_path, docx_path, or xlsx_path."}

        try:
            from blocks.document_engine.main import parse_all
            from blocks.document_engine.reasoner import DocumentReasoner
            from blocks.document_engine.mapper import DocumentMapper
            from blocks.document_engine.parsers.pdf_parser import PDFParser
            from blocks.document_engine.parsers.docx_parser import DOCXParser
            from blocks.document_engine.parsers.xlsx_parser import XLSXParser
            import yaml

            # Load config
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            config_path = os.path.join(project_root, "blocks", "document_engine", "config.yaml")
            if not os.path.exists(config_path):
                config_path = os.path.join(os.path.dirname(__file__), "..", "..", "blocks", "document_engine", "config.yaml")
                config_path = os.path.abspath(config_path)

            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    full_config = yaml.safe_load(f)
                config = full_config.get("document_engine", full_config)
            else:
                config = {}

            # ------------------------------------------------------------------
            # Layer 1: Parse — use platform blocks when available, fallback to own parsers
            # ------------------------------------------------------------------
            documents = []

            # PDF → platform pdf block → fallback to own PDFParser
            if file_paths.get("pdf"):
                pdf_text = None
                if self.config.get("use_platform_pdf", True):
                    pdf_text = await self._parse_with_platform_pdf(file_paths["pdf"])

                if pdf_text is not None:
                    from blocks.document_engine.parsers.pdf_parser import PDFDocument
                    doc = PDFDocument(source=file_paths["pdf"], text=pdf_text)
                    documents.append(doc)
                else:
                    parser = PDFParser(config)
                    documents.append(parser.parse(file_paths["pdf"]))

            # DOCX → own parser (no platform block available)
            if file_paths.get("docx"):
                parser = DOCXParser(config)
                documents.append(parser.parse(file_paths["docx"]))

            # XLSX → own parser (no platform block available)
            if file_paths.get("xlsx"):
                parser = XLSXParser(config)
                documents.append(parser.parse(file_paths["xlsx"]))

            # ------------------------------------------------------------------
            # Layer 2: Reason
            # ------------------------------------------------------------------
            reasoner = DocumentReasoner(config)
            reasoned = reasoner.reason(documents)

            # ------------------------------------------------------------------
            # Layer 3: Map
            # ------------------------------------------------------------------
            mapper = DocumentMapper(config)
            structured = mapper.map_to_structured(reasoned)

            result = structured.to_dict()
            result["status"] = "success"
            result["documents_parsed"] = len(documents)

            # Surface the OCR markup / redline verdict (Roadmap V2 · Epic 5).
            # If a scanned input carried coloured annotations, flag them rather
            # than presenting the extracted text as clean data.
            if self._last_ocr_markup is not None:
                result["markup"] = self._last_ocr_markup
                result["has_markup"] = bool(self._last_ocr_markup.get("has_markup"))

            result["platform_blocks_used"] = []
            if self.config.get("use_platform_pdf") and self.get_dep("pdf"):
                result["platform_blocks_used"].append("pdf")
            if self.config.get("use_platform_ocr") and self.get_dep("ocr"):
                result["platform_blocks_used"].append("ocr")
            return result

        except Exception as e:
            return {"status": "error", "error": f"Document engine pipeline failed: {str(e)}"}
