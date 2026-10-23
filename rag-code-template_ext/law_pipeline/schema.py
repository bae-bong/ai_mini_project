"""2026-10-23 팀 데이터 계약 중 법령(law) 메타데이터와 출처 매핑."""
from datetime import date, datetime, timedelta, timezone


def ingestion_time() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def validate_law_metadata(m: dict, *, indexed: bool = True) -> None:
    for key in ("doc_type", "doc_id", "title", "source_file", "law_name", "law_level", "category"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            raise ValueError(f"법령 메타데이터 필수 문자열 누락: {key}")
    if m["doc_type"] != "law" or m["doc_id"] != f"law:{m['law_name']}":
        raise ValueError("법령 doc_type/doc_id가 계약과 다릅니다.")
    if m["law_level"] not in {"법률", "시행령", "시행규칙"} or m["category"] not in {"노무", "우대기업"}:
        raise ValueError("law_level/category 값이 계약과 다릅니다.")
    for key, minimum in (("page", 1), ("chunk_index", 0)):
        if type(m.get(key)) is not int or m[key] < minimum:
            raise ValueError(f"법령 메타데이터 정수 범위 오류: {key}")
    for key in ("article_no", "article_title", "effective_date"):
        if key not in m or (m[key] is not None and (not isinstance(m[key], str) or not m[key].strip())):
            raise ValueError(f"선택 필드는 문자열 또는 null이어야 합니다: {key}")
    if m["effective_date"] is not None:
        parsed = date.fromisoformat(m["effective_date"])
        if parsed.isoformat() != m["effective_date"]:
            raise ValueError("effective_date는 YYYY-MM-DD여야 합니다.")
    if indexed or m.get("ingested_at") is not None:
        value = m.get("ingested_at")
        if not isinstance(value, str) or not value.endswith("+09:00"):
            raise ValueError("적재된 청크의 ingested_at은 +09:00 ISO 시각이어야 합니다.")
        datetime.fromisoformat(value)


def law_sources(hits: list[dict]) -> list[dict]:
    """검색 hit를 계약의 RAG sources 항목으로 변환; 각 청크의 점수 유지."""
    sources = []
    for hit in hits:
        m = hit["metadata"]
        if m.get("doc_type") != "law":
            raise ValueError("law_sources에는 법령 검색 결과만 전달하세요.")
        sources.append({"type": "law", "title": m["law_name"], "article": m["article_no"],
                        "page": m["page"], "score": hit["score"]})
    return sources
