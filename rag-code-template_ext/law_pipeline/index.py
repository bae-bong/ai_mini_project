"""pgvector law ingestion and hybrid retrieval, preserving the team metadata contract."""
from __future__ import annotations

import json
import os
import math
import re
from collections import Counter
from pathlib import Path

from .extract import digest, read_jsonl
from .schema import ingestion_time, validate_law_metadata, law_sources

DEFAULT_MODEL = "text-embedding-ada-002"  # Existing template's OpenAIEmbeddings default.


def embeddings(model: str, dimensions: int | None = None):
    from langchain_openai import OpenAIEmbeddings
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY가 필요합니다. 환경변수 또는 .env에 설정하세요.")
    kwargs = {"model": model, "max_retries": 2, "request_timeout": 60, "chunk_size": 64}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    return OpenAIEmbeddings(**kwargs)


def build_index(processed: Path, model: str = DEFAULT_MODEL,
                dimensions: int | None = None, replace: bool = False, embedding_client=None) -> dict:
    from langchain_core.documents import Document
    from core.vector_store import PgVectorStore

    existing = PgVectorStore("pdf").info()
    if existing and not replace:
        raise ValueError("기존 컬렉션이 있습니다. 백업 후 --replace를 사용하세요.")
    chunks = read_jsonl(processed / "chunks.jsonl")
    report = json.loads((processed / "report.json").read_text())
    if not chunks or any(c["metadata"]["status"] != "current" for c in chunks):
        raise ValueError("현행 청크가 없거나 비현행 청크가 섞여 있습니다.")
    if len({c["metadata"]["chunk_id"] for c in chunks}) != len(chunks):
        raise ValueError("청크 ID가 중복됩니다.")
    if {c["metadata"]["as_of"] for c in chunks} != {report["as_of"]}:
        raise ValueError("청크의 기준일이 일치하지 않습니다.")
    # Validate before API calls; only indexed exports receive the actual ingestion timestamp.
    ingested_at = ingestion_time()
    for chunk in chunks:
        chunk["metadata"]["ingested_at"] = ingested_at
        validate_law_metadata(chunk["metadata"])
    ordinals = {}
    for chunk in chunks:
        m = chunk["metadata"]
        key = (m["doc_id"], m["source_file"])
        expected = ordinals.get(key, 0)
        if m["chunk_index"] != expected:
            raise ValueError("chunk_index는 문서별로 0부터 연속해야 합니다.")
        ordinals[key] = expected + 1
    client = embedding_client or embeddings(model, dimensions)
    docs = [Document(page_content=c["page_content"], metadata=c["metadata"]) for c in chunks]
    vectors = client.embed_documents([d.page_content for d in docs])
    meta = {"format_version": 2, "managed_by": "law_pipeline",
            "metadata_contract": "law-2026-10-23", "ingested_at": ingested_at,
            "as_of": report["as_of"], "sources": report["sources"],
            "chunk_size": report["chunk_size"], "chunk_overlap": report["chunk_overlap"],
            "chunks_sha256": digest((processed / "chunks.jsonl").read_bytes())}
    store = PgVectorStore("pdf", model=model, dimensions=dimensions, embedding_client=client)
    return store.write_vectors(docs, vectors, mode="replace" if replace else "create",
                               manifest=meta, supplements=read_jsonl(processed / "supplements.jsonl"))


def load_index(embedding_client=None):
    from core.vector_store import PgVectorStore
    store = PgVectorStore("pdf", embedding_client=embedding_client)
    info = store.info()
    if not info:
        raise ValueError("법령 컬렉션이 없습니다. law_pipeline build를 먼저 실행하세요.")
    return PgVectorStore("pdf", model=info["embedding_model"],
                         dimensions=info["manifest"].get("requested_dimensions"),
                         embedding_client=embedding_client)


def lexical_scores(texts: list[str], question: str) -> list[float]:
    """Small-corpus BM25 with Korean 2/3-grams; no morphological service needed."""
    def tokens(text):
        terms = []
        for word in re.findall(r"[가-힣]+|[a-zA-Z]+|\d+", text.lower()):
            terms.append(word)
            if re.fullmatch(r"[가-힣]+", word):
                for size in (2, 3):
                    terms.extend(word[i:i + size] for i in range(len(word) - size + 1))
        return terms

    counts = [Counter(tokens(t)) for t in texts]
    query = set(tokens(question))
    df = Counter(term for count in counts for term in count)
    lengths = [sum(c.values()) for c in counts]
    avg = sum(lengths) / max(len(lengths), 1) or 1
    scores = []
    for count, length in zip(counts, lengths):
        score = 0.0
        for term in query:
            frequency = count[term]
            if not frequency:
                continue
            idf = math.log(1 + (len(counts) - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * length / avg))
        scores.append(score)
    return scores


def rank_hybrid(ranked, question):
    lexical = lexical_scores([doc.page_content for doc, _ in ranked], question)
    lexical_order = sorted(range(len(ranked)), key=lambda i: (-lexical[i], i))
    lexical_ranks = {i: pos + 1 for pos, i in enumerate(lexical_order) if lexical[i] > 0}
    fusion = {doc.metadata["chunk_id"]: 1 / (60 + i + 1) +
              (1 / (60 + lexical_ranks[i]) if i in lexical_ranks else 0)
              for i, (doc, _) in enumerate(ranked)}
    return sorted(ranked, key=lambda pair: -fusion[pair[0].metadata["chunk_id"]]), fusion


def search(question: str, k: int = 5, *, store=None, mode: str = "hybrid") -> dict:
    if mode not in {"hybrid", "dense"}:
        raise ValueError("mode는 hybrid 또는 dense여야 합니다.")
    store = store if store is not None else load_index()
    result = store.retrieve(question, k, hybrid=mode == "hybrid")
    hits = result["ranked"]
    law_ids = {d.metadata.get("law_id") for d, _ in hits}
    supplements = [r for r in result["supplements"] if r["law_id"] in law_ids]
    meta = result["manifest"]
    fusion = result.get("fusion", {})
    hit_rows = [{"text": d.page_content, "metadata": d.metadata, "score": float(s),
                 "retrieval_score": fusion.get(d.metadata["chunk_id"])} for d, s in hits]
    return {"question": question, "as_of": meta.get("as_of"), "retrieval_mode": mode,
            "score_type": "squared_L2_lower_is_closer", "ordering": "rrf_higher_first" if mode == "hybrid" else "L2_lower_first",
            "hits": hit_rows, "sources": law_sources(hit_rows),
            "supplementary_context": [{"text": r["text"], "metadata": {k: v for k, v in r.items()
                                       if k not in {"text", "spans"}}} for r in supplements],
            "needs_review": bool(supplements),
            "note": "검색 후보입니다. 적용조건·예외·부칙을 검토해야 하며 거리 점수는 법적 확신도가 아닙니다."}
