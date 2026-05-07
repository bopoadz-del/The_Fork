"""PDF Parser — Layer 1 syntactic extraction for document engine."""
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from pathlib import Path


@dataclass
class PDFDocument:
    source: str
    text: str = ""
    pages: List[str] = field(default_factory=list)
    tables: List[List[List[str]]] = field(default_factory=list)
    glossary: Dict[str, str] = field(default_factory=dict)
    figures: List[Dict[str, Any]] = field(default_factory=list)


class PDFParser:
    """Extract text, tables, glossary entries and figure captions from PDF."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

    def parse(self, file_path: str) -> PDFDocument:
        path = Path(file_path)
        doc = PDFDocument(source=str(path))

        # Try pymupdf first (already in root requirements), then pdfplumber, then PyPDF2
        parsed = False
        if not parsed:
            try:
                doc = self._parse_with_pymupdf(file_path, doc)
                parsed = True
            except Exception:
                pass

        if not parsed:
            try:
                doc = self._parse_with_pdfplumber(file_path, doc)
                parsed = True
            except Exception:
                pass

        if not parsed:
            try:
                doc = self._parse_with_pypdf2(file_path, doc)
                parsed = True
            except Exception:
                pass

        if not parsed:
            # Ultimate fallback — treat as text
            doc.text = path.read_text(errors="ignore")
            doc.pages = [doc.text]

        doc.glossary = self._extract_glossary(doc.text)
        doc.figures = self._extract_figures(doc.text)
        return doc

    def _parse_with_pymupdf(self, file_path: str, doc: PDFDocument) -> PDFDocument:
        import fitz  # pymupdf
        with fitz.open(file_path) as pdf:
            for page_num, page in enumerate(pdf):
                text = page.get_text()
                doc.pages.append(text)
                doc.text += text + "\n"
                # Table-like structures via text blocks
                blocks = page.get_text("blocks")
                page_tables = self._blocks_to_tables(blocks)
                doc.tables.extend(page_tables)
        return doc

    def _parse_with_pdfplumber(self, file_path: str, doc: PDFDocument) -> PDFDocument:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                doc.pages.append(text)
                doc.text += text + "\n"
                tables = page.extract_tables()
                if tables:
                    doc.tables.extend(tables)
        return doc

    def _parse_with_pypdf2(self, file_path: str, doc: PDFDocument) -> PDFDocument:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        for page in reader.pages:
            text = page.extract_text() or ""
            doc.pages.append(text)
            doc.text += text + "\n"
        return doc

    def _blocks_to_tables(self, blocks):
        """Heuristic: group aligned text blocks into pseudo-tables."""
        # Simplified — return empty; real table extraction handled by pdfplumber
        return []

    def _extract_glossary(self, text: str) -> Dict[str, str]:
        glossary = {}
        patterns = self.config.get("patterns", {}).get("glossary", [])
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                term = (
                    match.group("term").strip()
                    if "term" in match.groupdict()
                    else match.group(1).strip()
                )
                definition = (
                    match.group("definition").strip()
                    if "definition" in match.groupdict()
                    else match.group(2).strip()
                )
                if 1 < len(term) < 40:
                    glossary[term] = definition
        return glossary

    def _extract_figures(self, text: str) -> List[Dict[str, Any]]:
        figures = []
        fig_pattern = r"Figure\s+(\d+[.-]?\d*)\s*[—:\-\.]\s*(.+?)(?:\n|$)"
        for match in re.finditer(fig_pattern, text, re.IGNORECASE):
            figures.append({
                "id": match.group(1),
                "caption": match.group(2).strip(),
            })
        return figures
