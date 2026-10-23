"""
/rag-query 응답 구성 헬퍼 (feat/rag-response)
- search_law: 법령 검색 (pgvector + BM25 순위 결합)
- law_sources: 검색 결과 → 응답 sources (law_pipeline.schema.law_sources 와 같은 형식)
- judge_needs_review: 위험 질문·근거 약함 판정
- build_law_prompt: 근거 조문 + 페르소나를 넣은 프롬프트
"""
import os

NOT_FOUND = "자료에서 찾을 수 없음"
REVIEW_NOTICE = "※ 개별 사안의 법적 판단은 노무사 등 전문가 확인을 권장합니다."

# 개별 판단이 필요한 위험 질문 키워드
RISK_KEYWORDS = ("해고", "부당", "체불", "분쟁", "소송", "고소", "진정", "징계", "산재", "손해배상")
# squared L2 거리: 작을수록 유사. 최상위 결과가 이보다 멀면 근거가 약하다고 본다 (Day 3 평가로 조정)
WEAK_DISTANCE = float(os.getenv("RAG_WEAK_DISTANCE", "0.5"))


# 검색 방식: dense(의미 검색만) | hybrid(의미 + 키워드 BM25, 순위 합산 RRF)
SEARCH_MODE = os.getenv("RAG_SEARCH_MODE", "hybrid")


def search_law(vectorstore, question: str, k: int, mode: str | None = None):
    """[(Document, L2 거리)] 반환. 거리는 항상 의미 검색 원값(작을수록 유사)을 유지한다.

    hybrid: 법령 청크가 수백 개 수준이라 전체를 의미 검색으로 순위화한 뒤,
    도균님 law_pipeline 의 BM25(한글 2·3-gram) 점수 순위와 RRF로 합친다.
    점수 크기(L2 vs BM25)는 비교할 수 없어서 '순위'만 더한다.
    """
    mode = mode or SEARCH_MODE
    if mode != "hybrid":
        return vectorstore.similarity_search_with_score(question, k=k)

    from law_pipeline.index import lexical_scores

    total = int(vectorstore.index.ntotal)
    ranked = vectorstore.similarity_search_with_score(question, k=total)
    lexical = lexical_scores([d.page_content for d, _ in ranked], question)
    lex_order = sorted(range(len(ranked)), key=lambda i: (-lexical[i], i))
    lex_rank = {i: pos + 1 for pos, i in enumerate(lex_order) if lexical[i] > 0}
    fused = {i: 1 / (60 + i + 1) + (1 / (60 + lex_rank[i]) if i in lex_rank else 0)
             for i in range(len(ranked))}
    order = sorted(fused, key=lambda i: -fused[i])[:k]
    return [ranked[i] for i in order]


def search_law_pipeline(vectorstore, question: str, k: int, mode: str | None = None) -> dict:
    """pgvector 컬렉션 검색 (law_pipeline 계약: hits·sources·supplementary_context·needs_review).
    hybrid RRF는 위 search_law와 같은 방식이며 PgVectorStore.retrieve 안에서 수행된다."""
    from law_pipeline.index import search
    return search(question, k, store=vectorstore, mode=mode or SEARCH_MODE)


def cited_sources(sources: list[dict], answer: str, fallback: int = 3) -> list[dict]:
    """답변에 실제로 인용된 조문만 남긴다 (같은 법령·조문 중복 제거).
    - '제60조'가 '제60조의2'에 잘못 걸리지 않게 뒤에 '의'가 오면 제외
    - 답변에 법령명이 나오면 그 법령의 조문만 남김 (여러 법에 같은 조 번호가 있을 때)
    - 인용을 하나도 못 찾으면 상위 fallback개"""
    import re
    seen, uniq = set(), []
    for s in sources:
        key = (s.get("title"), s.get("article"))
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    cited = [s for s in uniq if s.get("article")
             and re.search(re.escape(s["article"]) + r"(?!의)", answer)]
    named = [s for s in cited if s.get("title") and s["title"] in answer]
    return named or cited or uniq[:fallback]


# 질문 재작성(multi-query): 일상어 질문을 법률 용어로 바꿔 한 번 더 검색
# 실험 결과(2026-10-23): 13번 미해결, 정확성 93.8→85.4%, 응답 2.0→3.4초 → 기본 꺼둠
QUERY_REWRITE = os.getenv("RAG_QUERY_REWRITE", "0") == "1"

_REWRITE_PROMPT = (
    "다음 질문을 한국 법령 조문에서 검색하기 좋은 검색어 한 줄로 바꿔라.\n"
    "- 일상어를 법령에 쓰이는 용어로 바꾼다 (예: 사장님→사용자, 직원→근로자, 월급→임금).\n"
    "- 질문에 없는 사실·수치·결론을 추가하지 않는다. 답을 쓰지 않는다.\n"
    "- 검색어만 출력한다.\n\n"
    "질문: {q}\n검색어:"
)


def rewrite_query(llm, question: str) -> str | None:
    try:
        out = llm.invoke(_REWRITE_PROMPT.format(q=question)).content.strip().splitlines()[0].strip()
        return out if out and out != question else None
    except Exception as e:
        print(f"[질문 재작성 실패] {e}")
        return None


def _doc_key(doc):
    m = doc.metadata or {}
    return (m.get("law_name") or m.get("title"), m.get("article_no"), m.get("item_no"), m.get("chunk_index"))


def search_law_multi(vectorstore, queries: list[str], k: int):
    """여러 검색어로 각각 hybrid 검색 → 순위 RRF 합산. 거리는 가장 가까운 값을 유지."""
    if len(queries) == 1:
        return search_law(vectorstore, queries[0], k)
    fused, best = {}, {}
    for q in queries:
        for rank, (doc, dist) in enumerate(search_law(vectorstore, q, k * 2)):
            key = _doc_key(doc)
            fused[key] = fused.get(key, 0) + 1 / (60 + rank + 1)
            if key not in best or dist < best[key][1]:
                best[key] = (doc, dist)
    order = sorted(fused, key=lambda key: -fused[key])[:k]
    return [best[key] for key in order]


def law_sources(docs_scores) -> list[dict]:
    """같은 법령·조문은 한 번만 (가장 가까운 점수 유지)."""
    seen, out = set(), []
    for doc, score in docs_scores:
        m = doc.metadata or {}
        name = m.get("law_name") or m.get("title") or m.get("source")
        key = (name, m.get("article_no"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "type": "law",
            "title": name,
            "article": m.get("article_no"),
            "page": m.get("page"),
            "score": round(float(score), 4),
        })
    return out


def judge_needs_review(question: str, docs_scores) -> tuple[bool, str | None]:
    hit = next((k for k in RISK_KEYWORDS if k in question), None)
    if hit:
        return True, f"개별 판단이 필요한 질문 ('{hit}')"
    if not docs_scores:
        return True, "검색 근거 없음"
    best = min(float(s) for _, s in docs_scores)
    if best > WEAK_DISTANCE:
        return True, f"근거 유사도 낮음 (최상위 거리 {best:.3f} > {WEAK_DISTANCE})"
    return False, None


def persona_note(persona: dict | None) -> str:
    if not persona:
        return "사용자 기업 정보: 미입력"
    emp = persona.get("employees")
    types = persona.get("company_types") or []
    parts = []
    if emp is not None:
        parts.append(f"상시근로자 {emp}명")
    if types:
        parts.append("기업 유형 " + ", ".join(types))
    return "사용자 기업 정보: " + (", ".join(parts) if parts else "미입력")


def build_law_context(docs_scores, max_chars: int = 12000) -> str:
    buf, total = [], 0
    for i, (doc, _) in enumerate(docs_scores, start=1):
        m = doc.metadata or {}
        label = " ".join(x for x in (m.get("law_name") or m.get("title"), m.get("article_no")) if x)
        text = (doc.page_content or "").strip()
        if not text:
            continue
        text = text[: max(0, max_chars - total)]
        if not text:
            break
        buf.append(f"[근거 {i}] {label}\n{text}")
        total += len(text)
    return "\n\n".join(buf)


# 프롬프트 버전: v1(근거 없으면 전체 거절) | v2(부분 답변 허용)
PROMPT_VERSION = os.getenv("RAG_PROMPT_VERSION", "v2")
PARTIAL_MISSING = "자료에 없음"

_RULE_NOT_FOUND = {
    "v1": "3. 근거에서 답을 찾을 수 없으면 정확히 '" + NOT_FOUND + "'이라고만 답한다.\n",
    "v2": ("3. 근거가 질문과 전혀 관련 없을 때만 정확히 '" + NOT_FOUND + "'이라고만 답한다.\n"
           "   질문의 일부만 근거가 있으면, 근거가 있는 부분은 출처와 함께 답하고 "
           "근거가 없는 부분은 '" + PARTIAL_MISSING + "'이라고 밝힌다.\n"
           "   부분 답변에는 '" + NOT_FOUND + "'라는 문구를 쓰지 않는다.\n"),
}


def build_law_prompt(question: str, context: str, persona: dict | None, version: str | None = None) -> str:
    rule3 = _RULE_NOT_FOUND.get(version or PROMPT_VERSION, _RULE_NOT_FOUND["v2"])
    return (
        "당신은 중소기업 경영지원 담당자를 돕는 노무·정책 안내 어시스턴트입니다.\n"
        "규칙:\n"
        "1. 아래 [근거]에 있는 내용만으로 한국어로 답한다. 근거에 없는 수치나 조건을 만들지 않는다.\n"
        "2. 답의 근거가 된 문장 끝에 (법령명 제n조) 형식으로 출처를 붙인다.\n"
        + rule3 +
        "4. 법이 세부 내용을 '대통령령'·'고용노동부령'에 위임하면, 위임 사실과 세부 기준은 시행령 확인이 필요하다고 밝힌다.\n"
        "5. 사용자 기업 정보(직원 수 등)에 따라 적용이 달라지는 조항이면 그 점을 짚는다. 정보가 없으면 가정하지 않는다.\n\n"
        f"{persona_note(persona)}\n\n"
        f"[근거]\n{context}\n\n"
        f"질문: {question}\n\n"
        "답변:"
    )


def retrieved_list(docs_scores) -> list[dict]:
    """답을 못 찾았을 때도 남기는 검색 후보 (원인 분석용: 검색 실패 vs LLM 판단)."""
    out = []
    for doc, score in docs_scores:
        m = doc.metadata or {}
        out.append({"title": m.get("law_name") or m.get("title"), "article": m.get("article_no"),
                    "score": round(float(score), 4)})
    return out


def persona_applied(persona: dict | None) -> dict | None:
    if not persona:
        return None
    return {
        "employees": persona.get("employees"),
        "company_types": persona.get("company_types") or [],
        "note": persona_note(persona),
    }
