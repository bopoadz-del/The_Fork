import os
import uuid
import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.dependencies import require_api_key, block_instances, _create_block_instance
from app.blocks import BLOCK_REGISTRY
from app.core import file_crypto

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", "10485760"))  # 10MB
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff",
    ".txt", ".md", ".csv", ".json", ".xml",
    ".mp3", ".mp4", ".wav", ".webm",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Construction-domain formats matched against the registered blocks:
    # drawing_qto (.dxf, .dwg), bim (.ifc), primavera_parser (.xer), MS Project (.mpp), Revit (.rvt).
    ".dxf", ".dwg", ".ifc", ".xer", ".mpp", ".rvt",
}

DATA_DIR = os.getenv("DATA_DIR", "./data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except PermissionError:
    # Fallback to temp dir if DATA_DIR is not writable
    import tempfile
    DATA_DIR = tempfile.gettempdir()


@router.post("/upload")
async def upload_v1(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = None,
    auth: dict = Depends(require_api_key),
):
    """File upload endpoint (v1 API).

    Accepts validated files and stores them. Returns URL for processing.

    When ``project_id`` is provided, ALSO:
    - Registers the file as a document in the project (so it survives across
      chat sessions and is reachable via /v1/projects/<id>/documents).
    - Schedules zvec indexing via doc_index.maybe_eager_index in the
      background so the chat can search the file content without the user
      having to re-upload it. This is what the previous "uploaded but not
      indexed" gap was — zvec is the engine, we just weren't calling it.
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

        # Save uploaded file — encrypted at rest iff DATA_ENCRYPTION_KEY is set
        # (opt-in; plaintext otherwise — see app/core/file_crypto.py).
        file.file.seek(0)
        file_crypto.write_document(filepath, file.file.read())

        base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
        response = {
            "url": f"{base_url}/static/{filename}",
            "filename": original_name,
            "stored_as": filename,
            "file_path": filepath,
            "size": file_size,
        }

        # If a project_id was supplied, persist + index the file so future
        # questions can search it via doc_index (zvec-backed TF-IDF). The
        # actual indexing runs as a FastAPI BackgroundTask so the upload
        # request returns immediately.
        if project_id and project_id.strip():
            try:
                from app.core import projects as projects_store
                from app.core import doc_index
                proj = projects_store.get_project(project_id, user_id=auth.get("user_id"))
                if proj is not None:
                    # Persist the file as a project document. Reuses the same
                    # storage path the upload already wrote to, so no copy.
                    doc = projects_store.add_document(
                        project_id=project_id,
                        original_name=original_name,
                        stored_as=filename,
                        file_path=filepath,
                        size=file_size,
                        role="user_upload",
                    )
                    response["project_id"] = project_id
                    response["document_id"] = doc.get("id") if isinstance(doc, dict) else None
                    response["indexed"] = True
                    response["indexing_status"] = "scheduled"
                    if background_tasks is not None and response["document_id"]:
                        background_tasks.add_task(
                            doc_index.maybe_eager_index,
                            project_id,
                            response["document_id"],
                        )
                else:
                    response["indexed"] = False
                    response["indexing_status"] = "skipped_project_not_found"
            except Exception as e:
                # Don't fail the upload if indexing scheduling fails —
                # the file is already stored. Surface the reason.
                logger.warning("Upload indexing failed: %s", e, exc_info=True)
                response["indexed"] = False
                response["indexing_status"] = f"error: {type(e).__name__}"
        else:
            response["indexed"] = False
            response["indexing_status"] = "no_project_id"

        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail="Upload failed")


@router.post("/v1/upload")
async def upload_v1_endpoint(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = None,
    auth: dict = Depends(require_api_key),
):
    """File upload endpoint (v1 API alias)."""
    return await upload_v1(file, project_id, background_tasks, auth)


# ---------------------------------------------------------------------------
# Document Engine Ingest Endpoint
# ---------------------------------------------------------------------------

async def _run_document_engine_block(saved_paths: dict, output_format: str = "json") -> dict:
    """Run the document_engine platform block on decrypted file paths."""
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
    return result


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
    from contextlib import ExitStack
    saved_paths = {}
    temp_files = []
    _stack = ExitStack()

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

            # Save to temp — encrypted at rest iff DATA_ENCRYPTION_KEY is set.
            file_id = str(uuid.uuid4())[:8]
            filename = f"{file_id}_{original_name}"
            filepath = os.path.join(DATA_DIR, filename)

            file_obj.file.seek(0)
            file_crypto.write_document(filepath, file_obj.file.read())

            saved_paths[key] = filepath
            temp_files.append(filepath)

        if not saved_paths:
            raise HTTPException(400, "No valid files provided. Upload at least one PDF/DOCX/XLSX")

        # The document_engine parsers (fitz / python-docx / openpyxl) read files
        # by raw path, so decrypt the stored files to plaintext temp paths for
        # the duration of the pipeline. open_plaintext is a no-op for plaintext
        # files (encryption disabled, or legacy files).
        saved_paths = {
            key: _stack.enter_context(file_crypto.open_plaintext(p))
            for key, p in saved_paths.items()
        }

        result = await _run_document_engine_block(saved_paths, output_format)

        if output_format == "yaml":
            import yaml as yaml_lib
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                content=yaml_lib.dump(result, sort_keys=False, allow_unicode=True),
                media_type="application/x-yaml",
                headers={"X-Documents-Parsed": str(result.get("documents_parsed", 0))},
            )

        return {
            "status": "success",
            "documents_parsed": result.get("documents_parsed", 0),
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
        logger.exception("ingest pipeline failed")
        raise HTTPException(status_code=500, detail="Ingest pipeline failed")
    finally:
        # Close any decrypt-to-temp copies created by open_plaintext.
        try:
            _stack.close()
        except Exception:
            pass
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
    from contextlib import ExitStack
    saved_paths = {}
    temp_files = []
    _stack = ExitStack()

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
            # Encrypted at rest iff DATA_ENCRYPTION_KEY is set (opt-in).
            file_obj.file.seek(0)
            file_crypto.write_document(filepath, file_obj.file.read())
            saved_paths[key] = filepath
            temp_files.append(filepath)

        if not saved_paths:
            raise HTTPException(400, "No valid files provided")

        # The document_engine block + its fallback parsers read files by raw
        # path; decrypt the stored files to plaintext temp paths for the run.
        # open_plaintext is a no-op when files are plaintext.
        saved_paths = {
            key: _stack.enter_context(file_crypto.open_plaintext(p))
            for key, p in saved_paths.items()
        }

        result = await _run_document_engine_block(saved_paths, output_format)

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
        logger.exception("ingest via block failed")
        raise HTTPException(status_code=500, detail="Ingest via block failed")
    finally:
        try:
            _stack.close()
        except Exception:
            pass
        for tmp in temp_files:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
