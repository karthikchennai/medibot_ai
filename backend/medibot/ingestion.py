"""Convert and index the assignment's PDFs/Markdown with Docling.

Run `python -m medibot.ingestion` while the embedded-Qdrant API is stopped.
Re-ingestion upserts stable IDs and removes superseded chunks for each source.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from medibot.embeddings import EmbeddingModels
from medibot.retrieval import expected_manifest, get_qdrant_client, read_manifest, write_manifest

if TYPE_CHECKING:
    from medibot.config import Settings

logger = logging.getLogger(__name__)


def source_files(data_dir: Path) -> list[Path]:
    from medibot.policy import COLLECTION_ROLES

    files = sorted(path for path in data_dir.rglob("*") if path.is_file() and path.suffix.lower() in {".pdf", ".md"})
    approved = []
    for path in files:
        collection = path.relative_to(data_dir).parts[0]
        if collection in COLLECTION_ROLES:
            approved.append(path)
        else:
            logger.warning("Skipping document outside an approved collection: %s", path.name)
    return approved


def _docling_pipeline(settings: Settings):
    """Import the heavy parser only inside the offline ingestion command."""
    settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(settings.model_cache_dir / "huggingface"))
    from docling.chunking import HybridChunker
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, LayoutObjectDetectionOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.settings import settings as docling_settings
    from docling.models.stages.table_structure.table_structure_model import TableStructureModel
    from docling.models.utils.hf_model_download import download_hf_model
    from docling_core.transforms.chunker.hierarchical_chunker import ChunkingDocSerializer, ChunkingSerializerProvider
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from docling_core.transforms.serializer.markdown import MarkdownTableSerializer, MarkdownParams
    from transformers import AutoTokenizer

    if "repeat_table_header" not in HybridChunker.model_fields:
        raise RuntimeError("This Docling version lacks table header repetition. Install the pinned requirements.")
    docling_settings.cache_dir = settings.model_cache_dir / "docling"

    class TableSerializerProvider(ChunkingSerializerProvider):
        def get_serializer(self, doc):
            return ChunkingDocSerializer(
                doc=doc, table_serializer=MarkdownTableSerializer(), params=MarkdownParams(compact_tables=True),
            )

    tokenizer = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(settings.embedding_model, cache_dir=str(settings.model_cache_dir)),
        max_tokens=448,
    )
    chunker = HybridChunker(
        tokenizer=tokenizer, merge_peers=True, repeat_table_header=True,
        serializer_provider=TableSerializerProvider(),
    )
    # Direct local directories avoid Hugging Face's nested snapshot paths,
    # which exceed Windows MAX_PATH inside the requested project location.
    artifacts_dir = settings.model_cache_dir / "docling"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    layout_options = LayoutObjectDetectionOptions()
    layout_spec = layout_options.model_spec
    download_hf_model(
        repo_id=layout_spec.repo_id, revision=layout_spec.revision,
        local_dir=artifacts_dir / layout_spec.repo_id.replace("/", "--"),
    )
    TableStructureModel.download_models(local_dir=artifacts_dir / TableStructureModel._model_repo_folder)
    options = PdfPipelineOptions()
    options.layout_options = layout_options
    options.artifacts_path = artifacts_dir
    # The supplied PDFs contain selectable text. Avoid an unnecessary OCR model;
    # Docling still uses layout/table structure recognition for their tables.
    options.do_ocr = False
    options.do_table_structure = True
    options.table_structure_options.do_cell_matching = True
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    return converter, chunker


def parse_document(path: Path, data_dir: Path, converter, chunker) -> list[dict]:
    from medibot.policy import COLLECTION_ROLES

    relative = path.relative_to(data_dir)
    collection = relative.parts[0]
    if collection not in COLLECTION_ROLES:
        raise ValueError(f"No access policy for collection: {collection}")
    result = converter.convert(source=path)
    status = str(getattr(result.status, "value", result.status))
    if status != "success":
        raise RuntimeError(f"Docling could not fully convert {relative.as_posix()}: {status}")
    document = result.document
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    chunks = []
    for ordinal, chunk in enumerate(chunker.chunk(dl_doc=document)):
        text = chunker.contextualize(chunk=chunk).strip()
        if not text:
            continue
        items = chunk.meta.doc_items or []
        pages = sorted({prov.page_no for item in items for prov in getattr(item, "prov", [])})
        labels = {str(getattr(item.label, "value", item.label)) for item in items}
        headings = list(chunk.meta.headings or [])
        if "table" in labels:
            chunk_type = "table"
        elif "code" in labels:
            chunk_type = "code"
        elif labels and labels.issubset({"section_header", "title"}):
            chunk_type = "heading"
        else:
            chunk_type = "text"
        source = relative.as_posix()
        chunk_id = str(uuid5(NAMESPACE_URL, f"medibot:{source}:{ordinal}:{text}"))
        chunks.append({
            "chunk_id": chunk_id,
            "text": text,
            "source_document": source,
            "source_sha256": source_hash,
            "collection": collection,
            "access_roles": list(COLLECTION_ROLES[collection]),
            "section_title": " > ".join(headings) or path.stem.replace("_", " ").title(),
            "headings": headings,
            "chunk_type": chunk_type,
            "page": pages[0] if pages else None,
            "page_number": pages[0] if pages else None,
            "pages": pages,
            "ordinal": ordinal,
        })
    if not chunks:
        raise RuntimeError(f"No searchable text was extracted from {relative.as_posix()}")
    return chunks


def ensure_collection(client: QdrantClient, settings: Settings, dimension: int) -> dict:
    expected = dict(expected_manifest(settings), dimension=dimension)
    exists = client.collection_exists(settings.qdrant_collection)
    if exists:
        current = read_manifest(client, settings)
        count = client.count(settings.qdrant_collection, exact=True).count
        if (current is None and count) or (current is not None and any(current.get(k) != v for k, v in expected.items())):
            raise RuntimeError("Existing index is incompatible. Set a new QDRANT_COLLECTION, then re-ingest.")
        parameters = client.get_collection(settings.qdrant_collection).config.params
        dense_config = parameters.vectors
        if not isinstance(dense_config, dict) or "dense" not in dense_config or dense_config["dense"].size != dimension:
            raise RuntimeError("Existing Qdrant collection has incompatible dense vectors.")
        if not parameters.sparse_vectors or "bm25" not in parameters.sparse_vectors:
            raise RuntimeError("Existing Qdrant collection is missing its BM25 vector.")
        if parameters.sparse_vectors["bm25"].modifier != models.Modifier.IDF:
            raise RuntimeError("Existing Qdrant BM25 vector lacks the required IDF modifier.")
    else:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config={"dense": models.VectorParams(size=dimension, distance=models.Distance.COSINE)},
            sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        if settings.qdrant_url:
            for field in ("access_roles", "collection", "source_document"):
                client.create_payload_index(settings.qdrant_collection, field, models.PayloadSchemaType.KEYWORD, wait=True)
    write_manifest(client, settings, dict(expected, ingestion_state="in_progress"))
    return expected


def index_document(client: QdrantClient, settings: Settings, embeddings, chunks: list[dict]) -> int:
    """Embed before touching the index, then remove only obsolete IDs of this source."""
    if not chunks:
        raise ValueError("Cannot index an empty document.")
    sources = {chunk["source_document"] for chunk in chunks}
    if len(sources) != 1:
        raise ValueError("Each indexing transaction must contain exactly one source document.")
    texts = [chunk["text"] for chunk in chunks]
    dense = embeddings.dense_documents(texts)
    sparse = embeddings.sparse_documents(texts)
    if len(dense) != len(chunks) or len(sparse) != len(chunks):
        raise RuntimeError("Embedding count does not match chunk count.")
    ensure_collection(client, settings, len(dense[0]))
    points = [
        models.PointStruct(id=chunk["chunk_id"], vector={"dense": d, "bm25": s}, payload=chunk)
        for chunk, d, s in zip(chunks, dense, sparse)
    ]
    for start in range(0, len(points), 64):
        client.upsert(settings.qdrant_collection, points=points[start:start + 64], wait=True)
    client.delete(
        settings.qdrant_collection,
        points_selector=models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="source_document", match=models.MatchValue(value=next(iter(sources))))],
            must_not=[models.HasIdCondition(has_id=[point.id for point in points])],
        )), wait=True,
    )
    return len(points)


def ingest(settings: Settings, data_dir: Path | None = None) -> dict:
    root = (data_dir or settings.data_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Dataset directory does not exist: {root}")
    files = source_files(root)
    if not files:
        raise RuntimeError("No PDF or Markdown documents were found in the approved collection folders.")
    client = get_qdrant_client()
    audit_dir = settings.qdrant_path.parent / "ingestion"
    audit_dir.mkdir(parents=True, exist_ok=True)
    report = {"documents": [], "chunks": 0, "collections": {}, "completed_at": None}
    counts: Counter = Counter()
    marker_written = False
    try:
        expected = expected_manifest(settings)
        previous = read_manifest(client, settings)
        if previous is not None and any(previous.get(k) != v for k, v in expected.items()):
            raise RuntimeError("Existing index is incompatible. Set a new QDRANT_COLLECTION, then re-ingest.")
        if previous is None and client.collection_exists(settings.qdrant_collection):
            if client.count(settings.qdrant_collection, exact=True).count:
                raise RuntimeError("Existing index has no manifest. Set a new QDRANT_COLLECTION, then re-ingest.")
        write_manifest(client, settings, {**(previous or expected), "ingestion_state": "in_progress"})
        marker_written = True
        converter, chunker = _docling_pipeline(settings)
        embeddings = EmbeddingModels(settings)
        for path in files:
            logger.info("Converting %s", path.relative_to(root).as_posix())
            chunks = parse_document(path, root, converter, chunker)
            indexed = index_document(client, settings, embeddings, chunks)
            counts[chunks[0]["collection"]] += indexed
            report["chunks"] += indexed
            report["documents"].append({
                "source_document": chunks[0]["source_document"], "chunks": indexed,
                "tables": sum(chunk["chunk_type"] == "table" for chunk in chunks),
            })
            # Auditable local extraction: never exposed through an unauthenticated route.
            output = audit_dir / (path.parent.name + "__" + path.stem + ".chunks.json")
            output.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Indexed %d chunks", indexed)
        report["collections"] = dict(counts)
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        (audit_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        manifest = {**expected_manifest(settings), **(read_manifest(client, settings) or {}),
                    "last_ingestion": report, "ingestion_state": "complete"}
        write_manifest(client, settings, manifest)
        return report
    except BaseException:
        if marker_written:
            try:
                manifest = read_manifest(client, settings) or expected_manifest(settings)
                write_manifest(client, settings, {**manifest, "ingestion_state": "failed"})
            except Exception:
                logger.exception("Could not update ingestion failure status; index remains incomplete.")
        raise
    finally:
        client.close()
        get_qdrant_client.cache_clear()


def main() -> None:
    from medibot.config import get_settings

    parser = argparse.ArgumentParser(description="Index MediBot documents with Docling and Qdrant hybrid vectors.")
    parser.add_argument("--data-dir", type=Path, help="Root containing general, clinical, nursing, billing, equipment.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(ingest(get_settings(), args.data_dir), indent=2))


if __name__ == "__main__":
    main()
