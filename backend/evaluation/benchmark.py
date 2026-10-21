"""Measure fixed passage-level retrieval targets without making LLM calls.

Run from backend: python -m evaluation.benchmark --output ../evidence/retrieval.json
The same cross-encoder reranks both dense and hybrid candidate sets. Annotation
targets are answer-bearing passages, not merely matching document filenames.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANDIDATES = 10
RERANK_TOP_K = 3
MODES = ("dense", "hybrid")
DEFAULT_QUESTIONS = Path(__file__).with_name("questions.json")


def normalize(value: str) -> str:
    """Ignore formatting differences, preserving words, numbers and order."""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def source_key(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def matching_targets(passage: dict, expected: list[dict]) -> list[int]:
    """Each target needs both its source and all answer-bearing content signals."""
    content = " " + normalize(passage.get("text", "")) + " "
    source = source_key(str(passage.get("source_document", "")))
    return [
        index for index, target in enumerate(expected)
        if source == source_key(target["source_document"])
        and all(" " + normalize(phrase) + " " in content for phrase in target["all_of"])
    ]


def score_passages(candidates: list[dict], reranked: list[dict], expected: list[dict]) -> dict:
    """Recall is over fixed annotated targets, not all relevant corpus chunks."""
    covered = {index for passage in candidates[:CANDIDATES] for index in matching_targets(passage, expected)}
    first_rank = next(
        (rank for rank, passage in enumerate(reranked[:RERANK_TOP_K], start=1)
         if matching_targets(passage, expected)), None,
    )
    return {
        "recall_at_10": len(covered) / len(expected),
        "hit_at_10": int(bool(covered)),
        "mrr_at_3": 1.0 / first_rank if first_rank is not None else 0.0,
        "first_relevant_rank_after_rerank": first_rank,
        "matched_target_indices_at_10": sorted(covered),
    }


def _read_source(path: Path) -> list[str]:
    if path.suffix.casefold() == ".md":
        return [path.read_text(encoding="utf-8")]
    if path.suffix.casefold() != ".pdf":
        raise ValueError(f"Unsupported evaluation source: {path.name}")
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            import pdfplumber
        except ImportError:
            # PDFium is already a Docling dependency in the application runtime.
            import pypdfium2

            document = pypdfium2.PdfDocument(str(path))
            pages = []
            try:
                for index in range(len(document)):
                    page = document[index]
                    textpage = page.get_textpage()
                    try:
                        pages.append(textpage.get_text_range())
                    finally:
                        textpage.close()
                        page.close()
            finally:
                document.close()
            return pages
        with pdfplumber.open(path) as document:
            return [page.extract_text() or "" for page in document.pages]
    return [page.extract_text() or "" for page in PdfReader(path).pages]


def validate_questions(question_set: dict, data_dir: Path) -> dict:
    """Fail before measurement if annotations lack source support or valid ACLs."""
    from medibot.policy import COLLECTION_ROLES

    questions = question_set.get("questions", [])
    if not questions:
        raise ValueError("The benchmark question set is empty.")
    ids: set[str] = set()
    corpus: dict[str, list[str]] = {}
    source_hashes: dict[str, str] = {}
    root = data_dir.resolve(strict=True)
    for case in questions:
        if case["id"] in ids:
            raise ValueError(f"Duplicate question id: {case['id']}")
        ids.add(case["id"])
        if not case.get("question", "").strip() or case.get("query_type") not in {"exact_term", "paraphrase"}:
            raise ValueError(f"Invalid query or type for {case['id']}")
        targets = case.get("expected", [])
        if not targets:
            raise ValueError(f"No passage targets for {case['id']}")
        for target in targets:
            source = source_key(target["source_document"])
            collection = source.split("/", maxsplit=1)[0]
            if case["collection"] != collection or case["role"] not in COLLECTION_ROLES.get(collection, ()):
                raise ValueError(f"Target collection or role is invalid for {case['id']}")
            if not target.get("all_of") or any(not normalize(item) for item in target["all_of"]):
                raise ValueError(f"Missing answer-bearing phrases for {case['id']}")
            path = (root / source).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("Evaluation sources must remain inside data_dir.")
            if source not in corpus:
                corpus[source] = _read_source(path)
                source_hashes[source] = hashlib.sha256(path.read_bytes()).hexdigest()
            pages = corpus[source]
            if "page" in target:
                page = int(target["page"])
                if not 1 <= page <= len(pages):
                    raise ValueError(f"Invalid page for {case['id']}")
                source_text = normalize(pages[page - 1])
            else:
                source_text = normalize("\n".join(pages))
            absent = [
                phrase for phrase in target["all_of"]
                if " " + normalize(phrase) + " " not in " " + source_text + " "
            ]
            if absent:
                raise ValueError(f"Unsupported annotation {case['id']} in {source}: {absent}")
    return {
        "validated_questions": len(questions),
        "collection_counts": dict(Counter(case["collection"] for case in questions)),
        "query_type_counts": dict(Counter(case["query_type"] for case in questions)),
        "source_sha256": source_hashes,
    }


def _inspect_passages(passages: list[dict], role: str) -> None:
    # A benchmark fails if unauthorized records appear; it never filters them out
    # after retrieval and hides a failure in the retrieval-layer permission gate.
    for passage in passages:
        if role not in passage.get("access_roles", []):
            raise PermissionError("Benchmark detected an unauthorized retrieved passage.")


def _passage_record(passage: dict, expected: list[dict]) -> dict:
    return {
        "id": passage.get("id"),
        "source_document": passage.get("source_document"),
        "section_title": passage.get("section_title"),
        "collection": passage.get("collection"),
        "chunk_type": passage.get("chunk_type"),
        "score": passage.get("score"),
        "retrieval_score": passage.get("retrieval_score"),
        "matched_target_indices": matching_targets(passage, expected),
        "text": passage.get("text", ""),
    }


def _summarize(records: list[dict]) -> dict:
    result: dict[str, Any] = {"question_count": len(records)}
    for mode in MODES:
        values = [record["modes"][mode] for record in records]
        result[mode] = {
            field: statistics.mean(item[field] for item in values)
            for field in ("recall_at_10", "hit_at_10", "mrr_at_3", "retrieval_ms", "rerank_ms", "total_ms")
        }
    result["hybrid_minus_dense"] = {
        field: result["hybrid"][field] - result["dense"][field]
        for field in ("recall_at_10", "mrr_at_3", "total_ms")
    }
    return result


def run_benchmark(retriever: Any, questions: list[dict], *, warmup: bool = True) -> dict:
    """Use one ready index, identical roles/budgets/reranker, and no generation."""
    state = retriever.status()
    if not state["ready"]:
        raise RuntimeError(state.get("reason") or "Run ingestion before the benchmark.")
    warmup_seconds = 0.0
    if warmup:
        start = time.perf_counter()
        first = questions[0]
        for mode in MODES:
            candidates = retriever.search(first["question"], first["role"], mode=mode, limit=CANDIDATES)
            _inspect_passages(candidates, first["role"])
            reranked = retriever.rerank(first["question"], candidates, top_k=RERANK_TOP_K)
            _inspect_passages(reranked, first["role"])
        warmup_seconds = time.perf_counter() - start
    records = []
    for index, case in enumerate(questions):
        record = {
            "id": case["id"], "question": case["question"], "role": case["role"],
            "collection": case["collection"], "query_type": case["query_type"],
            "expected": case["expected"], "modes": {},
        }
        # Alternate order to avoid consistently favoring either method's warm cache.
        for mode in MODES if index % 2 == 0 else tuple(reversed(MODES)):
            start = time.perf_counter()
            candidates = retriever.search(case["question"], case["role"], mode=mode, limit=CANDIDATES)
            retrieval_ms = (time.perf_counter() - start) * 1000
            _inspect_passages(candidates, case["role"])
            start_rerank = time.perf_counter()
            reranked = retriever.rerank(case["question"], candidates, top_k=RERANK_TOP_K)
            rerank_ms = (time.perf_counter() - start_rerank) * 1000
            _inspect_passages(reranked, case["role"])
            record["modes"][mode] = {
                **score_passages(candidates, reranked, case["expected"]),
                "retrieval_ms": retrieval_ms, "rerank_ms": rerank_ms,
                "total_ms": retrieval_ms + rerank_ms,
                "candidates": [_passage_record(passage, case["expected"]) for passage in candidates],
                "reranked": [_passage_record(passage, case["expected"]) for passage in reranked],
            }
        records.append(record)
        print(f"[{index + 1}/{len(questions)}] {case['id']}", flush=True)
    return {
        "index": state,
        "warmup_seconds_excluded_from_query_timings": warmup_seconds,
        "summary": _summarize(records),
        "by_collection": {
            group: _summarize([record for record in records if record["collection"] == group])
            for group in sorted({record["collection"] for record in records})
        },
        "by_query_type": {
            group: _summarize([record for record in records if record["query_type"] == group])
            for group in sorted({record["query_type"] for record in records})
        },
        "queries": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--data-dir", type=Path, help="Source documents; defaults to Settings.data_dir.")
    parser.add_argument("--output", type=Path, default=Path("../evidence/retrieval.json"))
    parser.add_argument("--validate-only", action="store_true", help="Check annotations against documents without loading models.")
    parser.add_argument("--no-warmup", action="store_true", help="Include model cold-start costs in the first measured query.")
    args = parser.parse_args()
    from medibot.config import get_settings

    settings = get_settings()
    question_bytes = args.questions.read_bytes()
    question_set = json.loads(question_bytes)
    source_validation = validate_questions(question_set, args.data_dir or settings.data_dir)
    if args.validate_only:
        print(json.dumps(source_validation, indent=2))
        return
    from medibot.retrieval import HybridRetriever

    retriever = HybridRetriever(settings)
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = run_benchmark(retriever, question_set["questions"], warmup=not args.no_warmup)
    finally:
        retriever.close()
    report = {
        "schema_version": 1,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "questions_sha256": hashlib.sha256(question_bytes).hexdigest(),
        "source_validation": source_validation,
        "method": {
            "candidate_k": CANDIDATES, "reranked_k": RERANK_TOP_K,
            "methods": "dense + cross-encoder versus dense/BM25 Qdrant RRF + the same cross-encoder",
            "recall_at_10": "Fraction of manually annotated passage targets matched among 10 candidates; macro mean across questions. This is not exhaustive corpus chunk recall.",
            "mrr_at_3": "Reciprocal rank of the first answer-bearing passage after reranking to top 3; zero if none. Macro mean across questions.",
            "relevance": "Exact source path AND every normalized all_of phrase must occur in the same passage; page/section hints are provenance rather than scoring shortcuts.",
            "generation": "None. This benchmark makes no Groq or other generative LLM calls.",
            "timing": "Wall-clock retrieval and rerank time; one pass per question, alternating method order. Optional warmup is excluded. Timings are diagnostic, not a load test.",
            "limitations": "Small development set with non-exhaustive phrase annotations; valid unannotated passages may score zero. No clinical accuracy or universal hybrid improvement claim.",
        },
        **result,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Saved measured results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
