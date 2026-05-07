"""PDF Block - Standalone PDF extraction"""
from blocks.base import LegoBlock
from typing import Dict, Any, List
import io

class PDFBlock(LegoBlock):
    """PDF text and table extraction"""
    name = "pdf"
    version = "1.0.0"
    requires = ["config"]
    layer = 3  # Domain layer
    tags = ["pdf", "document", "extraction", "domain"]
    default_config = {
        "extract_tables": True,
        "extract_images": False
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.ocr_enabled = config.get("ocr_enabled", True)
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "extract_text":
            return await self._extract_text(input_data)
        elif action == "extract_tables":
            return await self._extract_tables(input_data)
        elif action == "extract_images":
            return await self._extract_images(input_data)
        elif action == "merge":
            return await self._merge_pdfs(input_data)
        elif action == "split":
            return await self._split_pdf(input_data)
        return {"error": "Unknown action"}
    
    async def _extract_text(self, data: Dict) -> Dict:
        """Extract text from PDF"""
        pdf_bytes = data.get("pdf_bytes") or data.get("file")
        file_path = data.get("file_path")
        
        try:
            import PyPDF2
            
            if not pdf_bytes and file_path:
                with open(file_path, "rb") as f:
                    pdf_bytes = f.read()
            
            if not pdf_bytes:
                return {"error": "No PDF bytes or file_path provided"}
            
            pdf_file = io.BytesIO(pdf_bytes)
            reader = PyPDF2.PdfReader(pdf_file)
            
            text_by_page = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                text_by_page.append({"page": i + 1, "text": text})
            
            full_text = "\n\n".join([p["text"] for p in text_by_page if p["text"]])
            
            return {
                "text": full_text,
                "pages": len(reader.pages),
                "text_by_page": text_by_page,
                "metadata": {
                    "title": reader.metadata.title if reader.metadata else None,
                    "author": reader.metadata.author if reader.metadata else None,
                }
            }
            
        except ImportError:
            return {"error": "PyPDF2 not installed. Run: pip install PyPDF2"}
        except Exception as e:
            return {"error": f"PDF extraction failed: {str(e)}"}
    
    async def _extract_tables(self, data: Dict) -> Dict:
        """Extract tables from PDF"""
        pdf_bytes = data.get("pdf_bytes")
        
        try:
            import tabula
            import pandas as pd
            
            # Save to temp file
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_bytes)
                temp_path = f.name
            
            # Extract tables
            tables = tabula.read_pdf(temp_path, pages='all', multiple_tables=True)
            
            # Convert to dict
            tables_data = []
            for i, table in enumerate(tables):
                tables_data.append({
                    "table_index": i,
                    "rows": len(table),
                    "columns": len(table.columns),
                    "data": table.to_dict(orient='records')[:50]  # Limit rows
                })
            
            import os
            os.unlink(temp_path)
            
            return {
                "tables": tables_data,
                "count": len(tables_data)
            }
            
        except ImportError:
            return {"error": "tabula-py not installed. Run: pip install tabula-py"}
        except Exception as e:
            return {"error": f"Table extraction failed: {str(e)}"}
    
    async def _extract_images(self, data: Dict) -> Dict:
        """Extract images from PDF"""
        pdf_bytes = data.get("pdf_bytes")
        
        try:
            import fitz  # PyMuPDF
            
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            images = []
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images()
                
                for img_index, img in enumerate(image_list, start=1):
                    xref = img[0]
                    pix = fitz.Pixmap(doc, xref)
                    
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    
                    img_bytes = pix.tobytes("png")
                    images.append({
                        "page": page_num + 1,
                        "index": img_index,
                        "format": "png",
                        "bytes": img_bytes,
                        "size": len(img_bytes)
                    })
            
            return {
                "images": images,
                "count": len(images)
            }
            
        except ImportError:
            return {"error": "PyMuPDF not installed. Run: pip install PyMuPDF"}
        except Exception as e:
            return {"error": f"Image extraction failed: {str(e)}"}
    
    async def _merge_pdfs(self, data: Dict) -> Dict:
        """Merge multiple PDFs"""
        pdf_list = data.get("pdfs", [])  # List of PDF bytes
        
        try:
            import PyPDF2
            
            merger = PyPDF2.PdfMerger()
            
            for pdf_bytes in pdf_list:
                pdf_file = io.BytesIO(pdf_bytes)
                merger.append(pdf_file)
            
            output = io.BytesIO()
            merger.write(output)
            merger.close()
            
            return {
                "pdf_bytes": output.getvalue(),
                "pages": len(pdf_list)
            }
            
        except ImportError:
            return {"error": "PyPDF2 not installed"}
        except Exception as e:
            return {"error": f"Merge failed: {str(e)}"}
    
    async def _split_pdf(self, data: Dict) -> Dict:
        """Split PDF into pages"""
        pdf_bytes = data.get("pdf_bytes")
        pages = data.get("pages", [])  # List of page numbers, or empty for all
        
        try:
            import PyPDF2
            
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            
            if not pages:
                pages = range(len(reader.pages))
            
            page_pdfs = []
            for page_num in pages:
                writer = PyPDF2.PdfWriter()
                writer.add_page(reader.pages[page_num])
                
                output = io.BytesIO()
                writer.write(output)
                
                page_pdfs.append({
                    "page": page_num + 1,
                    "pdf_bytes": output.getvalue()
                })
            
            return {
                "pages": page_pdfs,
                "count": len(page_pdfs)
            }
            
        except ImportError:
            return {"error": "PyPDF2 not installed"}
        except Exception as e:
            return {"error": f"Split failed: {str(e)}"}
    
    def health(self) -> Dict:
        h = super().health()
        try:
            import PyPDF2
            h["pypdf2_available"] = True
        except ImportError:
            h["pypdf2_available"] = False
        return h
