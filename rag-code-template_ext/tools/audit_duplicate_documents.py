"""Read-only duplicate audit. Run: python -m tools.audit_duplicate_documents.

Compare whole documents within a collection, never isolated repeated clauses.
Missing source_file cannot safely identify a document and is reported separately.
"""
import hashlib
import json
from collections import defaultdict

from core.vector_store import connect


def duplicate_candidates(rows):
    documents = defaultdict(list)
    missing = 0
    for collection, content, metadata in rows:
        source = metadata.get("source_file")
        if not source:
            missing += 1
            continue
        # Exclude generated chunk IDs, filename and ingestion timestamps. Keep
        # legal/notice identity so equal boilerplate across documents is not merged.
        identity = {k: metadata.get(k) for k in (
            "doc_type", "law_id", "law_name", "notice_id", "category",
            "article_no", "effective_date", "as_of", "status",
            "apply_start", "apply_end", "company_types")}
        documents[(collection, source)].append(json.dumps(
            [content, identity], sort_keys=True, ensure_ascii=False))
    signatures = defaultdict(list)
    for (collection, source), chunks in documents.items():
        signature = hashlib.sha256(json.dumps(sorted(chunks), ensure_ascii=False).encode()).hexdigest()
        signatures[(collection, signature)].append(source)
    groups = [{"collection": collection, "fingerprint": signature,
               "source_files": sorted(files), "chunks_per_file": len(documents[(collection, files[0])])}
              for (collection, signature), files in sorted(signatures.items()) if len(files) > 1]
    return {"duplicate_candidates": groups, "chunks_without_source_file": missing,
            "note": "조회만 수행했습니다. 개요와 첨부는 별도 자료이며, 후보 확인 및 DB 백업 후 정리하세요."}


def main():
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rows = conn.execute("SELECT collection, content, metadata FROM rag_chunks").fetchall()
    print(json.dumps(duplicate_candidates(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
