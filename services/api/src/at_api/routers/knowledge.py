"""Knowledge base search endpoints (Doc 12 section 12.8).

The index is built once at application startup and held in memory. Loading the
embedding model takes several seconds, so doing it per request would make search
unusable; doing it lazily on first request would put that cost on an unlucky
user instead.
"""

# ruff: noqa: N818
# As in at_core.errors: these model domain failures and are named to read
# naturally at the raise site. The AppError base makes the hierarchy obvious.

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from at_core.errors import AppError, ErrorCode, NotFound

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class KnowledgeUnavailable(AppError):
    """Raised when the corpus index has not been built.

    A distinct 503 rather than an empty result: an empty list looks like "no
    documents match", which would be a lie, and would send a user hunting for a
    query problem that does not exist.
    """

    status = 503
    code = ErrorCode.INTERNAL
    title = "Knowledge base unavailable"


def get_index(request: Request) -> Any:
    index = getattr(request.app.state, "knowledge_index", None)
    if index is None or not index.ready:
        raise KnowledgeUnavailable(
            "The knowledge corpus is not indexed. Check that data/knowledge "
            "contains documents and that the embedding model is available."
        )
    return index


IndexDep = Annotated[Any, Depends(get_index)]


@router.get("/search", summary="Search the knowledge corpus")
async def search(
    index: IndexDep,
    q: str = Query(..., min_length=2, max_length=400, description="Search query"),
    limit: int = Query(5, ge=1, le=20),
    source_type: str | None = Query(
        None, description="Comma-separated filter: AMM, SOP, FAA, NASA"
    ),
) -> dict[str, Any]:
    """Hybrid dense + lexical search over maintenance and regulatory documents."""
    types = (
        [value.strip() for value in source_type.split(",") if value.strip()]
        if source_type
        else None
    )
    hits = index.search(q, limit=limit, source_types=types)

    return {
        "query": q,
        "count": len(hits),
        "results": [hit.to_dict() for hit in hits],
    }


@router.get("/documents", summary="List indexed documents")
async def list_documents(index: IndexDep) -> dict[str, Any]:
    """Corpus inventory, so a user can see what the system actually knows."""
    documents: dict[str, dict[str, Any]] = {}
    for chunk in index.chunks:
        entry = documents.setdefault(
            chunk.doc_id,
            {
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "source_type": chunk.source_type,
                "chunks": 0,
                "sections": [],
            },
        )
        entry["chunks"] += 1
        if chunk.section_path not in entry["sections"]:
            entry["sections"].append(chunk.section_path)

    return {
        "count": len(documents),
        "documents": sorted(documents.values(), key=lambda d: d["doc_id"]),
    }


@router.get("/chunks/{chunk_id}", summary="Fetch one chunk by id")
async def get_chunk(chunk_id: str, index: IndexDep) -> dict[str, Any]:
    """Resolve a citation back to its source text."""
    for chunk in index.chunks:
        if chunk.chunk_id == chunk_id:
            return {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "source_type": chunk.source_type,
                "section_path": chunk.section_path,
                "content": chunk.content,
                "task_codes": list(chunk.task_codes),
            }
    raise NotFound(f"No chunk with id '{chunk_id}'.")


@router.get("/stats", summary="Corpus statistics")
async def stats(index: IndexDep) -> dict[str, Any]:
    result: dict[str, Any] = index.stats()
    return result
