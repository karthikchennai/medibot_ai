"""Qdrant hybrid retrieval with authorization inside both search branches."""

from __future__ import annotations

from functools import lru_cache
from threading import RLock
from typing import TYPE_CHECKING, Literal

from qdrant_client import QdrantClient, models

from medibot.embeddings import EmbeddingModels, SPARSE_MODEL

if TYPE_CHECKING:
    from medibot.config import Settings

INDEX_SCHEMA_VERSION = 1
MANIFEST_ID = 1
_CLIENT_LOCK = RLock()


class IndexNotReadyError(RuntimeError):
    pass


def expected_manifest(settings: Settings) -> dict:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "embedding_model": settings.embedding_model,
        "sparse_model": SPARSE_MODEL,
        "dense_vector": "dense",
        "sparse_vector": "bm25",
        "chunker": "docling.HybridChunker",
        "chunk_max_tokens": 448,
    }


def manifest_collection(settings: Settings) -> str:
    return f"{settings.qdrant_collection}__manifest"


def read_manifest(client: QdrantClient, settings: Settings) -> dict | None:
    name = manifest_collection(settings)
    if not client.collection_exists(name):
        return None
    records = client.retrieve(name, ids=[MANIFEST_ID], with_payload=True, with_vectors=False)
    return dict(records[0].payload or {}) if records else None


def write_manifest(client: QdrantClient, settings: Settings, manifest: dict) -> None:
    name = manifest_collection(settings)
    if not client.collection_exists(name):
        client.create_collection(name, vectors_config={})
    client.upsert(name, points=[models.PointStruct(id=MANIFEST_ID, vector={}, payload=manifest)], wait=True)


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    from medibot.config import get_settings

    settings = get_settings()
    if settings.qdrant_url:
        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=30)
    settings.qdrant_path.mkdir(parents=True, exist_ok=True)
    # FastAPI synchronous handlers may run in different worker threads.
    return QdrantClient(path=str(settings.qdrant_path), force_disable_check_same_thread=True)


class HybridRetriever:
    def __init__(self, settings: Settings, client: QdrantClient | None = None, embeddings=None):
        self.settings = settings
        self._client = client
        self.embeddings = embeddings or EmbeddingModels(settings)

    @property
    def client(self) -> QdrantClient:
        # lru_cache allows concurrent executions on an initial cache miss; the
        # lock prevents two FastAPI threads opening the same embedded database.
        with _CLIENT_LOCK:
            return self._client if self._client is not None else get_qdrant_client()

    def status(self) -> dict:
        """Read index health without loading or downloading any ML model."""
        try:
            if not self.client.collection_exists(self.settings.qdrant_collection):
                return {"ready": False, "count": 0, "reason": "Run document ingestion first."}
            count = self.client.count(self.settings.qdrant_collection, exact=True).count
            manifest = read_manifest(self.client, self.settings)
            expected = expected_manifest(self.settings)
            compatible = manifest is not None and all(manifest.get(k) == v for k, v in expected.items())
            ingestion_state = (manifest or {}).get("ingestion_state", "unverified")
            complete = ingestion_state == "complete"
            reason = None
            if not compatible:
                reason = "Index model/configuration differs. Use a new QDRANT_COLLECTION and re-ingest."
            elif not complete:
                reason = f"Document ingestion is {ingestion_state}. Complete ingestion before starting the backend."
            elif not count:
                reason = "Run document ingestion first."
            return {
                "ready": count > 0 and compatible and complete,
                "count": count,
                "compatible": compatible,
                "ingestion_state": ingestion_state,
                "reason": reason,
                "embedding_model": self.settings.embedding_model,
                "reranker_model": self.settings.reranker_model,
                "backend": "server" if self.settings.qdrant_url else "embedded",
            }
        except Exception:
            # Health is publicly callable: do not expose local paths, API keys, or endpoints.
            return {"ready": False, "count": 0, "reason": "Qdrant index is unavailable."}

    @staticmethod
    def role_filter(role: str) -> models.Filter:
        from medibot.policy import COLLECTION_ROLES

        valid_roles = {item for roles in COLLECTION_ROLES.values() for item in roles}
        if role not in valid_roles:
            raise PermissionError("Unknown role.")
        return models.Filter(must=[models.FieldCondition(key="access_roles", match=models.MatchValue(value=role))])

    def search(
        self, question: str, role: str, mode: Literal["hybrid", "dense"] = "hybrid", limit: int | None = None,
    ) -> list[dict]:
        access = self.role_filter(role)
        if not question.strip():
            return []
        if mode not in {"hybrid", "dense"}:
            raise ValueError("mode must be 'hybrid' or 'dense'")
        state = self.status()
        if not state["ready"]:
            raise IndexNotReadyError(state["reason"])
        candidate_count = limit or self.settings.retrieval_candidates
        dense = self.embeddings.dense_query(question)
        if mode == "dense":
            response = self.client.query_points(
                collection_name=self.settings.qdrant_collection, query=dense, using="dense",
                query_filter=access, limit=candidate_count, with_payload=True, with_vectors=False,
            )
        else:
            sparse = self.embeddings.sparse_query(question)
            # A single Qdrant Query API request. Restricted records are excluded
            # before either candidate list is formed, then checked again at fusion.
            response = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                prefetch=[
                    models.Prefetch(query=dense, using="dense", filter=access, limit=candidate_count),
                    models.Prefetch(query=sparse, using="bm25", filter=access, limit=candidate_count),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF), query_filter=access,
                limit=candidate_count, with_payload=True, with_vectors=False,
            )
        return [dict(point.payload or {}, id=str(point.id), score=float(point.score)) for point in response.points]

    def rerank(self, question: str, candidates: list[dict], top_k: int | None = None) -> list[dict]:
        if not candidates:
            return []
        scores = self.embeddings.rerank(question, [item["text"] for item in candidates])
        if len(scores) != len(candidates):
            raise RuntimeError("Reranker returned a different number of scores than passages.")
        ranked = [dict(item, retrieval_score=item["score"], score=score) for item, score in zip(candidates, scores)]
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:top_k or self.settings.rerank_top_k]

    def retrieve(self, question: str, role: str) -> list[dict]:
        return self.rerank(question, self.search(question, role))

    def close(self) -> None:
        with _CLIENT_LOCK:
            if self._client is not None:
                self._client.close()
                self._client = None
            elif get_qdrant_client.cache_info().currsize:
                get_qdrant_client().close()
                get_qdrant_client.cache_clear()


@lru_cache(maxsize=1)
def get_retriever() -> HybridRetriever:
    from medibot.config import get_settings

    return HybridRetriever(get_settings())
