"""HTTP surface for the RAG layer — one route, narrowly scoped.

``POST /v1/rag/search`` is what dashboards, debugging, and external
callers use to inspect retrieval against a project's indexed docs. The
chat block does its own in-process retrieval via ``retriever.retrieve()``
and does not go through this route — adding HTTP hops between blocks
would be silly. The route is here for visibility, not for plumbing.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.routers.auth import require_api_key

router = APIRouter()


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Text to retrieve against")
    project_id: str = Field(..., min_length=1, description="Scope to one project")
    k: int = Field(5, ge=1, le=50, description="Number of chunks to return")


class RagSearchChunk(BaseModel):
    chunk_id: str
    doc_id: str
    chunk_index: int
    text: str
    score: float


class RagSearchResponse(BaseModel):
    chunks: List[RagSearchChunk]
    count: int
    available: bool
    fast_search: bool  # True when sqlite-vec is loaded; False = numpy fallback


@router.post("/v1/rag/search", response_model=RagSearchResponse)
async def rag_search(
    req: RagSearchRequest,
    auth: dict = Depends(require_api_key),
) -> RagSearchResponse:
    """Search the project's indexed chunks for the query.

    Returns ``available=false`` when the embedding stack isn't installed
    (install via ``pip install -r requirements-rag.txt``); status code
    is still 200 so dashboards can render the empty state without
    treating it as an error.
    """
    from app.core.rag import retriever as _r
    from app.core.rag.vector_store import get_store
    from app.core.rag.embeddings import EMBEDDING_DIM

    if not _r.available():
        return RagSearchResponse(
            chunks=[], count=0, available=False, fast_search=False,
        )

    try:
        chunks = _r.retrieve(req.query, req.project_id, k=req.k)
    except ValueError as exc:
        # Caller-side error (e.g. empty project_id) — surface as 400
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        # Unexpected — log + 503 so dashboards can surface "retrieval down"
        raise HTTPException(status_code=503, detail=f"retrieval failed: {exc}")

    store = get_store(dim=EMBEDDING_DIM)
    return RagSearchResponse(
        chunks=[
            RagSearchChunk(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                chunk_index=c.chunk_index,
                text=c.text,
                score=float(c.score or 0.0),
            )
            for c in chunks
        ],
        count=len(chunks),
        available=True,
        fast_search=store.fast_search,
    )
