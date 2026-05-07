"""PDF Block - Extract text, tables, images, layout from PDF files."""

import io
import os
import base64
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig

class PDFBlock(BaseBlock):
    """Extract text, tables, images, and metadata from PDF files with layout preservation."""

    def __init__(self):
        super().__init__(BlockConfig(
            name="pdf",
            version="1.1",
            description="Extract text, tables, images, and metadata from PDF files with layout preservation",
            supported_inputs=["file", "file_path", "source_id"],
            supported_outputs=["text", "tables", "images", "metadata", "pages"]
        ,
            layer=3,
            tags=["domain", "documents", "pdf"]))
        self._pymupdf_available = self._check_pymupdf()

    def _check_pymupdf(self) -> bool:
        try:
            import fitz  # PyMuPDF
            return True
        except ImportError:
            return False

    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Main processing logic — this is the only part you change per block."""
        params = params or {}
        extract_text = params.get("extract_text", True)
        extract_tables = params.get("extract_tables", True)
        extract_images = params.get("extract_images", False)
        extract_metadata = params.get("extract_metadata", True)

        file_path = self._get_file_path(input_data)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        result = {
            "file_path": file_path,
            "file_size": os.path.getsize(file_path),
        }

        if self._pymupdf_available:
            import fitz
            doc = fitz.open(file_path)

            # Metadata
            if extract_metadata:
                result["metadata"] = {
                    **doc.metadata,
                    "page_count": len(doc),
                    "total_words": 0
                }

            # Text + layout (pages)
            if extract_text:
                full_text = ""
                pages = []
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = page.get_text("text")
                    pages.append({
                        "page_number": page_num + 1,
                        "text": text,
                        "word_count": len(text.split()),
                        "bbox": page.rect  # layout info
                    })
                    full_text += text + "\n\n"
                result["text"] = full_text.strip()
                result["pages"] = pages
                result["metadata"]["total_words"] = len(full_text.split())

            # Tables
            if extract_tables:
                tables = []
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    # PyMuPDF built-in table detection
                    tabs = page.find_tables()
                    for tab in tabs:
                        tables.append({
                            "page_number": page_num + 1,
                            "data": tab.extract(),
                            "bbox": tab.bbox
                        })
                result["tables"] = tables
                result["table_count"] = len(tables)

            # Images
            if extract_images:
                images = []
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    for img_index, img in enumerate(page.get_images(), start=1):
                        xref = img[0]
                        pix = fitz.Pixmap(doc, xref)
                        if pix.n > 4:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        img_data = pix.tobytes("png")
                        images.append({
                            "page_number": page_num + 1,
                            "image_index": img_index,
                            "format": "png",
                            "width": pix.width,
                            "height": pix.height,
                            "size_bytes": len(img_data),
                            "base64": base64.b64encode(img_data).decode("utf-8")
                        })
                result["images"] = images
                result["image_count"] = len(images)

            doc.close()
        else:
            result["text"] = f"[PDF content - PyMuPDF not installed. File: {file_path}]"

        result["confidence"] = 0.98 if self._pymupdf_available else 0.4
        return result

    def _get_file_path(self, input_data: Any) -> str:
        """Works with both direct file_path and our ingest source_id pattern."""
        if isinstance(input_data, dict):
            if "file_path" in input_data:
                return input_data["file_path"]
            if "source_id" in input_data:
                return f"/app/data/{input_data['source_id']}"
        if isinstance(input_data, str):
            return input_data
        raise ValueError("Invalid input: expected file path or dict with file_path/source_id")
