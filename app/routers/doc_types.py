"""Document-type registry API — Roadmap V2 · Epic 2.

List built-in types, register custom ones at runtime, and classify a document
by filename + content — all without a code change.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import doc_types
from app.dependencies import require_api_key

router = APIRouter()


class MatchRule(BaseModel):
    filename: List[str] = []
    extensions: List[str] = []
    content: List[str] = []


class DocumentTypeRequest(BaseModel):
    name: str
    description: str = ""
    match: MatchRule = MatchRule()
    expected_fields: List[str] = []


class ClassifyRequest(BaseModel):
    filename: str = ""
    content_sample: str = ""


@router.get("/v1/document-types")
async def list_document_types(auth: dict = Depends(require_api_key)):
    """List every document type — built-in and custom."""
    return {"document_types": doc_types.list_types()}


@router.post("/v1/document-types", status_code=201)
async def add_document_type(
    req: DocumentTypeRequest, auth: dict = Depends(require_api_key)
):
    """Register a custom document type — extends the registry, no redeploy."""
    if not req.name.strip():
        raise HTTPException(400, "Document type 'name' is required")
    try:
        return doc_types.add_type({
            "name": req.name,
            "description": req.description,
            "match": req.match.model_dump(),
            "expected_fields": req.expected_fields,
        })
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/v1/document-types/{name}")
async def delete_document_type(name: str, auth: dict = Depends(require_api_key)):
    """Remove a custom document type (built-ins cannot be removed)."""
    if not doc_types.remove_type(name):
        raise HTTPException(
            404, f"Custom document type '{name}' not found "
                 f"(built-in types cannot be removed)"
        )
    return {"status": "deleted", "name": name}


@router.post("/v1/document-types/classify")
async def classify_document(
    req: ClassifyRequest, auth: dict = Depends(require_api_key)
):
    """Classify a document by filename + content sample."""
    return doc_types.classify(req.filename, req.content_sample)
