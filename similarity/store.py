"""
ChromaDB-backed ticket store.

* Persistent client under ``CHROMA_DB_PATH`` (default: ``<backend>/chroma_db``).
* Embeddings: OpenAI ``text-embedding-3-small`` when ``OPENAI_API_KEY`` is set,
  otherwise Chroma's bundled local MiniLM model (ONNX). The embedder name is part
  of the collection name, so switching embedders never mixes vector dimensions.
* Cosine distance; similarity = 1 - distance.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils import embedding_functions

from .parser import Ticket, iter_batches

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BACKEND_DIR / "chroma_db"
COLLECTION_BASE = "similarity_tickets"
UPSERT_BATCH_SIZE = 100


def _embedder():
    """Return (embedding_function, tag)."""
    mode = os.environ.get("SIMILARITY_EMBEDDING", "auto").lower()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    use_openai = mode == "openai" or (mode == "auto" and bool(api_key))
    if use_openai:
        if not api_key:
            raise RuntimeError("SIMILARITY_EMBEDDING=openai but OPENAI_API_KEY is not set")
        model = os.environ.get("SIMILARITY_EMBEDDING_MODEL", "text-embedding-3-small")
        ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=api_key, model_name=model
        )
        return ef, f"openai_{model.replace('-', '_').replace('.', '_')}"
    return embedding_functions.DefaultEmbeddingFunction(), "local_minilm"


def _and(conditions: List[dict]) -> Optional[dict]:
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


class TicketStore:
    def __init__(self, db_path: Optional[str] = None):
        self._path = str(db_path or os.environ.get("CHROMA_DB_PATH") or DEFAULT_DB_PATH)
        self._lock = threading.RLock()
        self._client = None
        self._collection = None
        self._meta_cache: Optional[List[Dict[str, Any]]] = None
        self._ef = None
        self.embedder_tag = ""

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def collection(self):
        if self._collection is None:
            with self._lock:
                if self._collection is None:
                    Path(self._path).mkdir(parents=True, exist_ok=True)
                    self._client = chromadb.PersistentClient(path=self._path)
                    ef, tag = _embedder()
                    self._ef = ef
                    self.embedder_tag = tag
                    self._collection = self._client.get_or_create_collection(
                        name=f"{COLLECTION_BASE}_{tag}"[:63],
                        embedding_function=ef,
                        metadata={"hnsw:space": "cosine"},
                    )
                    logger.info(
                        "Chroma collection ready: %s (%d docs) at %s",
                        self._collection.name, self._collection.count(), self._path,
                    )
        return self._collection

    def count(self) -> int:
        return self.collection.count()

    def reset(self) -> None:
        with self._lock:
            col = self.collection
            name = col.name
            ef, _ = _embedder()
            self._client.delete_collection(name)
            self._collection = self._client.get_or_create_collection(
                name=name, embedding_function=ef, metadata={"hnsw:space": "cosine"}
            )
            self._meta_cache = None

    # ── write ────────────────────────────────────────────────────────────────
    def upsert(self, tickets: List[Ticket]) -> int:
        total = 0
        with self._lock:
            _ = self.collection  # make sure the client + embedder are initialised
            for batch in iter_batches(tickets, UPSERT_BATCH_SIZE):
                self.collection.upsert(
                    ids=[t.ticket_id for t in batch],
                    documents=[t.description for t in batch],
                    # embed the problem headline, display the full description
                    embeddings=self._ef([t.embed_text for t in batch]),
                    metadatas=[t.metadata() for t in batch],
                )
                total += len(batch)
            self._meta_cache = None
        return total

    # ── read ─────────────────────────────────────────────────────────────────
    def search(
        self,
        query: str,
        n_results: int = 10,
        filters: Optional[List[dict]] = None,
    ) -> List[Dict[str, Any]]:
        total = self.count()
        if total == 0:
            return []
        res = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, total),
            where=_and(filters or []),
            include=["documents", "metadatas", "distances"],
        )
        hits: List[Dict[str, Any]] = []
        for tid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            hits.append({**meta, "ticket_id": tid, "description": doc,
                         "similarity": max(0.0, 1.0 - float(dist))})
        return hits

    def get_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids or self.count() == 0:
            return []
        res = self.collection.get(ids=ids, include=["documents", "metadatas"])
        by_id = {
            tid: {**meta, "ticket_id": tid, "description": doc, "similarity": 1.0}
            for tid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"])
        }
        return [by_id[i] for i in ids if i in by_id]

    def all_metadata(self) -> List[Dict[str, Any]]:
        """All ticket metadata (cached) — used for analytics and filter vocab."""
        with self._lock:
            if self._meta_cache is None:
                if self.count() == 0:
                    self._meta_cache = []
                else:
                    res = self.collection.get(include=["metadatas"])
                    self._meta_cache = [
                        {**m, "ticket_id": tid} for tid, m in zip(res["ids"], res["metadatas"])
                    ]
            return self._meta_cache

    def vocabulary(self, field: str) -> List[str]:
        return sorted({str(m.get(field, "")) for m in self.all_metadata() if m.get(field)})


_store: Optional[TicketStore] = None
_store_lock = threading.Lock()


def get_store() -> TicketStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TicketStore()
    return _store
