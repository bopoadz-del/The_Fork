import os
import shutil
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.dependencies import require_api_key, block_instances, _create_block_instance
from app.blocks import BLOCK_REGISTRY

router = APIRouter()

MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", "10485760"))  # 10MB
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".txt", ".md", ".csv", ".json", ".xml",
    ".mp3", ".mp4", ".wav", ".webm",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"
}

DATA_DIR = os.getenv("DATA_DIR", "./data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except PermissionError:
    # Fallback to temp dir if DATA_DIR is not writable (e.g. Render without disk)
    import tempfile
    DATA_DIR = tempfile.gettempdir()


@router.post("/upload")
async def upload_v1(file: UploadFile = File(...), auth: dict = Depends(require_api_key)):
    """File upload endpoint (v1 API).

    Accepts validated files and stores them. Returns URL for processing.
    """
    try:
        # Validate file size
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
        if file_size > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail=f"File too large. Max size: {MAX_UPLOAD_SIZE} bytes")

        # Validate and sanitize filename
        original_name = (file.filename or "unknown").strip()
        if not original_name or original_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")

        # Prevent path traversal
        original_name = os.path.basename(original_name.replace("\\", "/"))
        _, ext = os.path.splitext(original_name.lower())
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed")

        # Generate unique filename
        file_id = str(uuid.uuid4())[:8]
        filename = f"{file_id}_{original_name}"
        filepath = os.path.join(DATA_DIR, filename)

        # Save uploaded file
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Return URL and server path for chain processing
        base_url = os.getenv("API_BASE_URL", "https://cerebrum-platform-api.onrender.com")
        return {
            "url": f"{base_url}/static/{filename}",
            "filename": original_name,
            "stored_as": filename,
            "file_path": filepath,
            "size": file_size
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Upload failed")


@router.post("/v1/upload")
async def upload_v1_endpoint(file: UploadFile = File(...), auth: dict = Depends(require_api_key)):
    """File upload endpoint (v1 API alias)."""
    return await upload_v1(file, auth)


# ---------------------------------------------------------------------------
# Document Engine Ingest Endpoint
# ---------------------------------------------------------------------------

class IngestResponse(BaseModel):
    status: str
    documents_parsed: int
    glossary_count: int
    requirements_count: int
    constraints_count: int
    schedule_targets_count: int
    equipment_specs_count: int
    risks_count: int
    output: dict


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    pdf: Optional[UploadFile] = File(None, description="PDF document (BOD, spec, drawing)"),
    docx: Optional[UploadFile] = File(None, description="Word document (RFP, contract)"),
    xlsx: Optional[UploadFile] = File(None, description="Excel file (schedule template, BOQ)"),
    output_format: str = "json",
    auth: dict = Depends(require_api_key),
):
    """Document Engine — Parse → Reason → Map pipeline.

    Upload technical documents and run the 3-layer reasoning pipeline:
    Layer 1 (Parse): Extract text, tables, glossary, figures
    Layer 2 (Reason): 8 semantic pipelines (requirements, constraints, risks, etc.)
    Layer 3 (Map): Structured output + downstream feeds for schedule/cost/risk engines

    Returns structured JSON/YAML consumable by downstream blocks.
    """
    saved_paths = {}
    temp_files = []

    try:
        # --- Layer 0: Accept & persist uploads ---
        for file_obj, key in [(pdf, "pdf"), (docx, "docx"), (xlsx, "xlsx")]:
            if file_obj is None:
                continue

            # Validate extension
            original_name = (file_obj.filename or "unknown").strip()
            _, ext = os.path.splitext(original_name.lower())
            if ext not in (".pdf", ".docx", ".xlsx", ".doc", ".xls"):
                raise HTTPException(400, f"File type '{ext}' not supported for ingest")

            # Save to temp
            file_id = str(uuid.uuid4())[:8]
            filename = f"{file_id}_{original_name}"
            filepath = os.path.join(DATA_DIR, filename)

            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(file_obj.file, buffer)

            saved_paths[key] = filepath
            temp_files.append(filepath)

        if not saved_paths:
            raise HTTPException(400, "No valid files provided. Upload at least one PDF/DOCX/XLSX")

        # --- Layer 1-3: Run pipeline ---
        from blocks.document_engine.main import parse_all
        from blocks.document_engine.reasoner import DocumentReasoner
        from blocks.document_engine.mapper import DocumentMapper
        import yaml

        config_path = os.path.join(DATA_DIR, "..", "blocks", "document_engine", "config.yaml")
        config_path = os.path.abspath(config_path)
        if not os.path.exists(config_path):
            # Fallback: resolve from project root
            config_path = os.path.join(os.path.dirname(__file__), "..", "..", "blocks", "document_engine", "config.yaml")
            config_path = os.path.abspath(config_path)

        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                full_config = yaml.safe_load(f)
            config = full_config.get("document_engine", full_config)
        else:
            config = {}

        documents = parse_all(saved_paths, config)

        reasoner = DocumentReasoner(config)
        reasoned = reasoner.reason(documents)

        mapper = DocumentMapper(config)
        structured = mapper.map_to_structured(reasoned)

        result = structured.to_dict()
        result["status"] = "success"
        result["documents_parsed"] = len(documents)

        if output_format == "yaml":
            import json
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                content=structured.to_yaml(),
                media_type="application/x-yaml",
                headers={"X-Documents-Parsed": str(len(documents))}
            )

        return {
            "status": "success",
            "documents_parsed": len(documents),
            "glossary_count": len(result.get("glossary", {})),
            "requirements_count": len(result.get("requirements", [])),
            "constraints_count": len(result.get("constraints", [])),
            "schedule_targets_count": len(result.get("schedule_targets", [])),
            "equipment_specs_count": len(result.get("equipment_specs", [])),
            "risks_count": len(result.get("risks", [])),
            "output": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingest pipeline failed: {str(e)}")
    finally:
        # Cleanup temp files
        for tmp in temp_files:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass


@router.post("/ingest-via-block")
async def ingest_via_block(
    pdf: Optional[UploadFile] = File(None, description="PDF document"),
    docx: Optional[UploadFile] = File(None, description="Word document"),
    xlsx: Optional[UploadFile] = File(None, description="Excel file"),
    output_format: str = "json",
    auth: dict = Depends(require_api_key),
):
    """Document Engine via platform block registry (dependencies auto-wired).

    Same pipeline as /ingest but routes through BLOCK_REGISTRY so platform
    blocks (pdf, ocr) are resolved and injected automatically.
    """
    saved_paths = {}
    temp_files = []

    try:
        for file_obj, key in [(pdf, "pdf"), (docx, "docx"), (xlsx, "xlsx")]:
            if file_obj is None:
                continue
            original_name = (file_obj.filename or "unknown").strip()
            _, ext = os.path.splitext(original_name.lower())
            if ext not in (".pdf", ".docx", ".xlsx", ".doc", ".xls"):
                raise HTTPException(400, f"File type '{ext}' not supported")
            file_id = str(uuid.uuid4())[:8]
            filename = f"{file_id}_{original_name}"
            filepath = os.path.join(DATA_DIR, filename)
            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(file_obj.file, buffer)
            saved_paths[key] = filepath
            temp_files.append(filepath)

        if not saved_paths:
            raise HTTPException(400, "No valid files provided")

        # Resolve or create the document_engine block instance
        if "document_engine" not in block_instances:
            block_instances["document_engine"] = _create_block_instance(BLOCK_REGISTRY["document_engine"])

        block = block_instances["document_engine"]
        result = await block.process({
            "pdf_path": saved_paths.get("pdf"),
            "docx_path": saved_paths.get("docx"),
            "xlsx_path": saved_paths.get("xlsx"),
        }, {"output_format": output_format})

        if result.get("status") == "error":
            raise HTTPException(500, result.get("error", "Document engine failed"))

        if output_format == "yaml":
            from fastapi.responses import PlainTextResponse
            import yaml as yaml_lib
            return PlainTextResponse(
                content=yaml_lib.dump(result, sort_keys=False, allow_unicode=True),
                media_type="application/x-yaml",
            )

        return {
            "status": "success",
            "documents_parsed": result.get("documents_parsed", 0),
            "platform_blocks_used": result.get("platform_blocks_used", []),
            "output": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingest via block failed: {str(e)}")
    finally:
        for tmp in temp_files:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
