"""LangChain FAISS format, with reproducible model metadata and supplements."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import math
import re
from collections import Counter
from pathlib import Path

from .extract import digest, read_jsonl, write_jsonl
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


def build_index(processed: Path, destination: Path, model: str = DEFAULT_MODEL,
                dimensions: int | None = None, replace: bool = False, embedding_client=None) -> dict:
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    if destination.exists() and any(destination.iterdir()) and not replace:
        raise ValueError("기존 인덱스가 있습니다. 내용을 확인한 뒤 --replace로 재생성하세요.")
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
    vectorstore = FAISS.from_documents(docs, client, ids=[c["metadata"]["chunk_id"] for c in chunks])
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Finish all files in a sibling directory before replacing an existing index.
    temp = Path(tempfile.mkdtemp(prefix=".law-index-", dir=destination.parent))
    backup = destination.with_name(destination.name + ".previous")
    try:
        vectorstore.save_local(str(temp))
        shutil.copy2(processed / "supplements.jsonl", temp / "supplements.jsonl")
        write_jsonl(temp / "chunks.jsonl", chunks)
        meta = {"format_version": 1, "metadata_contract": "law-2026-10-23", "ingested_at": ingested_at, "embedding_model": model, "requested_dimensions": dimensions,
                "vector_dimension": int(vectorstore.index.d), "vectors": int(vectorstore.index.ntotal),
                "as_of": report["as_of"], "sources": report["sources"],
                "chunk_size": report["chunk_size"], "chunk_overlap": report["chunk_overlap"],
                "chunks_sha256": digest((processed / "chunks.jsonl").read_bytes()),
                "files": {name: digest((temp / name).read_bytes()) for name in
                          ["index.faiss", "index.pkl", "supplements.jsonl", "chunks.jsonl"]}}
        (temp / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        if destination.exists():
            if backup.exists():
                raise ValueError(f"이전 백업이 이미 있습니다: {backup}; 먼저 별도 보관하세요.")
            destination.rename(backup)
        try:
            temp.rename(destination)
        except Exception:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        return meta
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def load_index(directory: Path, embedding_client=None):
    """Only load indexes produced locally by this project; pickle is not an interchange format."""
    from langchain_community.vectorstores import FAISS
    meta = json.loads((directory / "manifest.json").read_text())
    for name in meta["files"]:
        if digest((directory / name).read_bytes()) != meta["files"][name]:
            raise ValueError(f"인덱스 파일 해시 불일치: {name}")
    client = embedding_client or embeddings(meta["embedding_model"], meta["requested_dimensions"])
    # Hashes detect accidental mixing, NOT a malicious pickle and forged manifest.
    store = FAISS.load_local(str(directory), client, allow_dangerous_deserialization=True)
    if store.index.d != meta["vector_dimension"] or store.index.ntotal != meta["vectors"]:
        raise ValueError("인덱스 크기가 manifest와 다릅니다.")
    return store


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


def search(directory: Path, question: str, k: int = 5, *, store=None, mode: str = "hybrid") -> dict:
    if not question.strip() or not 1 <= k <= 20:
        raise ValueError("질문은 비어 있을 수 없고 k는 1~20이어야 합니다.")
    if mode not in {"hybrid", "dense"}:
        raise ValueError("mode는 hybrid 또는 dense여야 합니다.")
    store = store if store is not None else load_index(directory)
    # These statutes are a small corpus. Searching all vectors also provides
    # honest L2 distances for lexical candidates, rather than inventing scores.
    ranked = store.similarity_search_with_score(question, k=int(store.index.ntotal) if mode == "hybrid" else k)
    fusion = {}
    if mode == "hybrid":
        lexical = lexical_scores([doc.page_content for doc, _ in ranked], question)
        lexical_order = sorted(range(len(ranked)), key=lambda i: (-lexical[i], i))
        lexical_ranks = {i: pos + 1 for pos, i in enumerate(lexical_order) if lexical[i] > 0}
        # Reciprocal rank fusion uses ranks, not incomparable BM25/L2 magnitudes.
        fusion = {doc.metadata["chunk_id"]: 1 / (60 + i + 1) +
                  (1 / (60 + lexical_ranks[i]) if i in lexical_ranks else 0)
                  for i, (doc, _) in enumerate(ranked)}
        ranked.sort(key=lambda pair: -fusion[pair[0].metadata["chunk_id"]])
    hits = ranked[:k]
    law_ids = {d.metadata["law_id"] for d, _ in hits}
    # Addenda can qualify otherwise-current text, e.g. a temporarily lower employee limit.
    # Return them separately, without pretending they were similarity-ranked hits.
    supplements = [r for r in read_jsonl(directory / "supplements.jsonl") if r["law_id"] in law_ids]
    meta = json.loads((directory / "manifest.json").read_text())
    hit_rows = [{"text": d.page_content, "metadata": d.metadata, "score": float(s),
                 "retrieval_score": fusion.get(d.metadata["chunk_id"])} for d, s in hits]
    return {"question": question, "as_of": meta["as_of"], "retrieval_mode": mode,
            "score_type": "squared_L2_lower_is_closer", "ordering": "rrf_higher_first" if mode == "hybrid" else "L2_lower_first",
            "hits": hit_rows, "sources": law_sources(hit_rows),
            "supplementary_context": [{"text": r["text"], "metadata": {k: v for k, v in r.items()
                                       if k not in {"text", "spans"}}} for r in supplements],
            "needs_review": bool(supplements),
            "note": "검색 후보입니다. 적용조건·예외·부칙을 검토해야 하며 거리 점수는 법적 확신도가 아닙니다."}
