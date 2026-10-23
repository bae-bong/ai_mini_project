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


def search_law(vectorstore, question: str, k: int):
    """법령 파이프라인의 검색·출처·부칙 계약을 API에서도 그대로 사용한다."""
    from law_pipeline.index import search
    return search(question, k, store=vectorstore)



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


def build_law_prompt(question: str, context: str, persona: dict | None) -> str:
    return (
        "당신은 중소기업 경영지원 담당자를 돕는 노무·정책 안내 어시스턴트입니다.\n"
        "규칙:\n"
        "1. 아래 [근거]에 있는 내용만으로 한국어로 답한다. 근거에 없는 수치나 조건을 만들지 않는다.\n"
        "2. 답의 근거가 된 문장 끝에 (법령명 제n조) 형식으로 출처를 붙인다.\n"
        "3. 근거에서 답을 찾을 수 없으면 정확히 '" + NOT_FOUND + "'이라고만 답한다.\n"
        "4. 법이 세부 내용을 '대통령령'·'고용노동부령'에 위임하면, 위임 사실과 세부 기준은 시행령 확인이 필요하다고 밝힌다.\n"
        "5. 사용자 기업 정보(직원 수 등)에 따라 적용이 달라지는 조항이면 그 점을 짚는다. 정보가 없으면 가정하지 않는다.\n\n"
        f"{persona_note(persona)}\n\n"
        f"[근거]\n{context}\n\n"
        f"질문: {question}\n\n"
        "답변:"
    )


def persona_applied(persona: dict | None) -> dict | None:
    if not persona:
        return None
    return {
        "employees": persona.get("employees"),
        "company_types": persona.get("company_types") or [],
        "note": persona_note(persona),
    }
