"""Lazy, local ONNX embedding and cross-encoder models.

Groq is used only for answer/SQL generation. Documents stay local while these
models create their searchable representations; the first use downloads weights.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from qdrant_client import models

if TYPE_CHECKING:
    from medibot.config import Settings

SPARSE_MODEL = "Qdrant/bm25"


class EmbeddingModels:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._dense = None
        self._sparse = None
        self._reranker = None
        self._lock = RLock()

    def _cache(self) -> str:
        self.settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
        return str(self.settings.model_cache_dir)

    @property
    def dense(self):
        with self._lock:
            if self._dense is None:
                from fastembed import TextEmbedding

                self._dense = TextEmbedding(
                    model_name=self.settings.embedding_model, cache_dir=self._cache(),
                    threads=4,
                )
            return self._dense

    @property
    def sparse(self):
        with self._lock:
            if self._sparse is None:
                from fastembed import SparseTextEmbedding

                cache_dir = self._cache()
                repository = Path(cache_dir) / "models--Qdrant--bm25"
                revision_file = repository / "refs" / "main"
                local_options = {}
                if revision_file.is_file():
                    snapshots = (repository / "snapshots").resolve()
                    snapshot = (snapshots / revision_file.read_text(encoding="utf-8").strip()).resolve()
                    # English BM25 only reads english.txt. FastEmbed's generic
                    # completeness check also requires unused language files and
                    # a mock model file, triggering unnecessary remote requests.
                    if snapshot.is_relative_to(snapshots) and (snapshot / "english.txt").is_file():
                        local_options["specific_model_path"] = str(snapshot)
                self._sparse = SparseTextEmbedding(
                    model_name=SPARSE_MODEL, cache_dir=cache_dir, language="english", **local_options,
                )
            return self._sparse

    @property
    def cross_encoder(self):
        with self._lock:
            if self._reranker is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._reranker = TextCrossEncoder(
                    model_name=self.settings.reranker_model, cache_dir=self._cache(),
                    threads=4,
                )
            return self._reranker

    def dense_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.dense.passage_embed(texts, batch_size=32)]

    def sparse_documents(self, texts: list[str]) -> list[models.SparseVector]:
        return [self._sparse_vector(vector) for vector in self.sparse.passage_embed(texts)]

    def dense_query(self, question: str) -> list[float]:
        return next(self.dense.query_embed(question)).tolist()

    def sparse_query(self, question: str) -> models.SparseVector:
        return self._sparse_vector(next(self.sparse.query_embed(question)))

    def rerank(self, question: str, texts: list[str]) -> list[float]:
        return [float(score) for score in self.cross_encoder.rerank(question, texts)]

    @staticmethod
    def _sparse_vector(vector) -> models.SparseVector:
        return models.SparseVector(indices=vector.indices.tolist(), values=vector.values.tolist())
