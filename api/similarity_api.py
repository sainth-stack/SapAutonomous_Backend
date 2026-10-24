"""
Similarity Tickets API (ChromaDB).

POST /api/similarity/query    chatbot question -> KEDB-style answer + ticket table
GET  /api/similarity/stats    ticket counts by year x priority (+ reference check)
POST /api/similarity/ingest   upload a CSV/TSV ticket export into ChromaDB
GET  /api/similarity/health   store status

Endpoints are plain ``def`` so FastAPI runs them in a worker thread — the
Chroma / OpenAI calls are blocking.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from similarity.analytics import priority_matrix
from similarity.ingest import ingest_text, validate_counts
from similarity.service import MAX_QUERY_CHARS, answer_query
from similarity.store import get_store

logger = logging.getLogger(__name__)

similarity_router = APIRouter(prefix="/api/similarity", tags=["similarity-tickets"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class SimilarityQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)


def _require_admin(token: Optional[str]) -> None:
    """Ingest overwrites data and spends embedding credits: optionally token-protect it."""
    expected = os.environ.get("SIMILARITY_ADMIN_TOKEN", "").strip()
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Token")


@similarity_router.post("/query")
def query_tickets(req: SimilarityQuery):
    try:
        return answer_query(req.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Similarity query failed")
        raise HTTPException(status_code=500, detail=f"Similarity search failed: {exc}")


@similarity_router.get("/stats")
def ticket_stats():
    store = get_store()
    metadata = store.all_metadata()
    return {
        "total": len(metadata),
        "by_year_priority": priority_matrix(metadata),
        "validation": validate_counts(),
    }


@similarity_router.get("/health")
def similarity_health():
    store = get_store()
    try:
        count = store.count()
        return {"status": "ok", "tickets": count, "embedder": store.embedder_tag,
                "collection": store.collection.name}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Chroma unavailable: {exc}")


@similarity_router.post("/ingest")
async def ingest_tickets(
    file: UploadFile = File(..., description="CSV or TSV with Ticket Creation Date, Time, Ticket ID, Priority, Ticket Description"),
    reset: bool = Form(False),
    x_admin_token: Optional[str] = Header(None),
):
    _require_admin(x_admin_token)
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    from starlette.concurrency import run_in_threadpool

    try:
        return await run_in_threadpool(ingest_text, text, reset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Ticket ingest failed")
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}")
