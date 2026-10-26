#### 다음 실습 코드는 학습 목적으로만 사용 바랍니다. 문의 : audit@korea.ac.kr 임성열 Ph.D.

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Depends
from core.auth import get_current_user
from fastapi.responses import JSONResponse
from langchain_text_splitters import RecursiveCharacterTextSplitter
from core.vector_store import PgVectorStore
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel, Field
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from pypdf import PdfReader
from langchain_core.messages import HumanMessage
from fastapi import Form
from rag_meta import build_law_chunks, build_notice_chunks
from rag_answer import (search_law, law_sources, judge_needs_review, build_law_context,
                        build_law_prompt, persona_applied, NOT_FOUND, REVIEW_NOTICE,
                        retrieved_list, PROMPT_VERSION,
                        QUERY_REWRITE, search_law_pipeline, cited_sources,
                        USE_SYNONYMS, expand_query,
                        rank_notices_for_persona, build_notice_context, build_notice_prompt, notice_sources,
                        is_not_found)

# 패키지는 저자 업데이트에 맞춰 해당 버전으로 맞추는 것을 권장
# 직접 저자의 site-packages/패키지명/을 직접 수정하는 것은 추천하지 않음
# 추후 pip install -U 시 원상 복구되며 버전 충돌 가능성 있음angchain.text_splitter 
# 참고로 다음은 Deprecated 패키지 또는 미사용 패키지 목록
# from langchain_community.embeddings import HuggingFaceEmbeddings
# from langchain.vectorstores import FAISS
# from langchain.chat_models import ChatOpenAI
# from langchain.embeddings import OpenAIEmbeddings
# from langchain_openai import OpenAIEmbeddings, ChatOpenAI
# from langchain_community.embeddings import OpenAIEmbeddings
# from langchain.chat_models import ChatOpenAI
# from langchain.schema import HumanMessage
# from langchain.embeddings import HuggingFaceEmbeddings  
# from pydantic import BaseModel

from typing import List, Dict, Any, Optional
import os
import re
import json
import asyncio
import tempfile
import hashlib
import unicodedata
from datetime import datetime, UTC
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import os
from pathlib import Path
from dotenv import load_dotenv

# 현재 Python 파일과 같은 위치에 있는 .env 파일 로드
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 답변 재현성: 같은 질문에 같은 답 (평가 수치 안정). 실험 비교 시 RAG_TEMPERATURE 로 변경
LLM_TEMPERATURE = float(os.getenv("RAG_TEMPERATURE", "0"))
# seed: OpenAI의 best-effort 결정성 (temperature 0만으로는 답이 흔들림)
LLM_SEED = int(os.getenv("RAG_SEED", "42"))

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

# ✅ FastAPI 인스턴스
app = FastAPI(
    title="RAG 기반 Closed Source LLM API Server",
    description="외부 정보 기반 벡터화 + LLM 질의 응답",
    version="1.0.0"
)
@app.get("/health", tags=["상태"])
def health():
    try:
        pdf = VECTORSTORE_PDF.info()
        web = VECTORSTORE_WEB.info()
    except Exception:
        raise HTTPException(status_code=503, detail="PostgreSQL 상태 확인 실패")
    return {"status": "ok", "storage": "pgvector",
            "pdf_index": bool(pdf and pdf["vectors"]),
            "web_index": bool(web and web["vectors"])}

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174",
                   "http://127.0.0.1:5174",
                   "http://[::1]:5174",         # IPv6도 허용
                   "http://localhost:8001"],
    allow_methods=["*"], allow_headers=["*"], allow_credentials=True,
)

# ✅ 공통 오류 처리 + 요청 로그
from core.errors import register_error_handlers
from core.logging_mw import add_logging_middleware
register_error_handlers(app)
add_logging_middleware(app)


# ✅ 로그인 사용자·페르소나 확인 (인증 필요)
@app.get("/me", summary="토큰의 사용자·페르소나 확인", tags=["공통"])
async def me(current_user: dict = Depends(get_current_user)):
    return current_user

# Collections are queried from PostgreSQL on every request; CLI ingestion is
# visible immediately without restarting the RAG server.
VECTORSTORE_WEB = PgVectorStore("web")
VECTORSTORE_PDF = PgVectorStore("pdf")

# ✅ PDF 텍스트 추출
def load_paper(file_path: str) -> str:
    reader = PdfReader(file_path)
    return "\n".join(text for page in reader.pages if (text := page.extract_text()))

# ✅ 웹 검색 → 외부 정보 벡터화
# @app.post("/upload-topic", summary="웹 검색 → 외부 정보 벡터화", tags=["외부 정보"])
async def upload_topic(
    topic: str = Query(..., description="검색할 주제"),
    current_user: dict = Depends(get_current_user),
):
    try:
        import os, re, aiohttp
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from langchain_openai import OpenAIEmbeddings

        # ---- 1) 네이버 뉴스 검색: headers/params 전달 + 페이지네이션 ----
        NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
        NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

        search_url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }

        want = 20          # 수집 목표 건수
        display = 20       # 요청 건수(최대 100 가능, 요금/정책 고려)
        start = 1
        items = []

        async with aiohttp.ClientSession() as session:
            while len(items) < want:
                params = {
                    "query": topic,
                    "display": display,
                    "start": start,     # 1, 21, 41 … (display에 맞춰 증가)
                    "sort": "date",
                }
                async with session.get(search_url, headers=headers, params=params) as resp:
                    # 상태 코드 확인 (401/429 등 조기 감지)
                    resp.raise_for_status()
                    data = await resp.json()

                batch = data.get("items", [])
                if not batch:
                    break

                # 네이버는 <b>…</b> 태그를 포함 → 제거
                def strip_tags(s: str) -> str:
                    return re.sub(r"</?b>", "", s or "")

                for it in batch:
                    it["title"] = strip_tags(it.get("title", ""))
                    it["description"] = strip_tags(it.get("description", ""))
                items.extend(batch)

                start += display
                if start > 1000:  # API 한계 보호
                    break

        if not items:
            return {"message": "검색 결과가 없습니다.", "collected": 0}

        # ---- 2) 텍스트 구성(제목+요약 위주) ----
        texts = [
            f"title: {it.get('title','')}\nlink: {it.get('link','')}\ndesc: {it.get('description','')}"
            for it in items
        ]
        # 너무 긴 텍스트는 잘라서 임베딩 안정화
        texts = [t[:3000] for t in texts]

        # ---- 3) 텍스트 분할 ----
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)
        docs = splitter.create_documents(texts)

        if not docs:
            return {"message": "벡터화할 문서가 없습니다.", "collected": len(items)}

        # Keep this legacy collector disabled; persist its results in pgvector.
        vectors = VECTORSTORE_WEB._client().embed_documents([d.page_content for d in docs])
        VECTORSTORE_WEB.write_vectors(docs, vectors, mode="replace")

        return {
            "message": f"{len(docs)}개의 웹 문서(청크) 벡터화 및 저장 완료",
            "collected": len(items),
            "sample_titles": [it.get("title", "") for it in items[:3]],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ✅ PDF 업로드 및 벡터화
async def read_uploaded_pdf(file):
    filename = unicodedata.normalize("NFC", (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1])
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 허용됩니다.")
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="파일당 20MB 이하만 허용됩니다.")
    if not data or not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="유효한 PDF 파일을 선택하세요.")
    file.filename = filename
    return data


@app.post("/upload-paper", summary="법령 PDF 업로드 및 벡터화 (조 단위)", tags=["PDF 처리"])
async def upload_pdf(
    file: UploadFile = File(...),
    category: str = Form("노무", description="노무 | 우대기업"),
    current_user: dict = Depends(get_current_user),
):
    if category not in {"노무", "우대기업"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 법령 분류입니다.")
    info = VECTORSTORE_PDF.info()
    if info and info["manifest"].get("managed_by") == "law_pipeline":
        raise HTTPException(status_code=409, detail="법령 컬렉션은 law_pipeline build로 관리됩니다.")
    try:
        data = await read_uploaded_pdf(file)
        upload_hash = hashlib.sha256(data).hexdigest()
        if VECTORSTORE_PDF.duplicate_upload(upload_hash):
            return {"skipped": True, "message": "이미 등록된 동일 자료입니다."}
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            if not load_paper(tmp_path).strip():
                raise HTTPException(status_code=422, detail="PDF에서 텍스트를 추출하지 못했습니다.")
            chunks = build_law_chunks(tmp_path, file.filename, category)
        finally:
            os.unlink(tmp_path)

        if not chunks or not any(c["page_content"].strip() for c in chunks):
            raise HTTPException(status_code=422, detail="PDF에서 텍스트를 추출하지 못했습니다. 스캔 PDF라면 OCR이 필요합니다.")

        # [Q01 개선] 고정 500자 → 조 단위 청킹(최대 1,000자, 중첩 150) + '법령명 조문(제목)' 머리글
        docs = [Document(page_content=c["page_content"], metadata=c["metadata"]) for c in chunks]

        for doc in docs:
            doc.metadata["upload_hash"] = upload_hash
        result = VECTORSTORE_PDF.add_documents(docs, source_file=file.filename)
        if result.get("skipped"):
            return result

        return {
            "message": f"{len(docs)}개 조문 청크 적재 완료",
            "law_name": docs[0].metadata["law_name"],
            "articles": sum(1 for d in docs if d.metadata["article_no"]),
            "sample_metadata": docs[min(5, len(docs) - 1)].metadata,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload-notice", summary="지원사업 공고(meta JSON + 첨부 PDF) 적재·덮어쓰기", tags=["공고"])
async def upload_notice(
    meta: Optional[str] = Form(None, description="공고 메타 JSON. 생략 시 PDF 파일명으로 기본 공고를 생성합니다."),
    file: Optional[UploadFile] = File(None, description="공고 첨부 PDF (선택)"),
    current_user: dict = Depends(get_current_user),
):
    """같은 notice_id + 파일명으로 다시 올리면 해당 청크만 교체된다.
    - 개요(meta) 청크: source_file = '{notice_id}/_meta'
    - 첨부 PDF 청크: source_file = '{notice_id}/{파일명}'"""
    try:
        meta_obj = json.loads(meta) if meta else {}
        if not isinstance(meta_obj, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="meta는 JSON 객체 문자열이어야 합니다.")
    if file is not None and file.filename and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="첨부는 PDF만 허용됩니다. (HWP는 tools/hwp_to_pdf.py로 변환)")

    tmp_path = None
    try:
        data = await read_uploaded_pdf(file) if file is not None and file.filename else None
        if not meta:
            if data is None:
                raise HTTPException(status_code=400, detail="공고 PDF 또는 메타 정보가 필요합니다.")
            # Stable filename identity replaces a revised upload; hash also detects renamed duplicates.
            meta_obj = {"notice_id": "upload-" + hashlib.sha256(file.filename.encode()).hexdigest()[:32],
                        "title": file.filename[:-4]}
        upload_hash = hashlib.sha256((data or b"") + (json.dumps(meta_obj, sort_keys=True,
                        ensure_ascii=False).encode() if meta else b"")).hexdigest()
        if VECTORSTORE_WEB.duplicate_upload(upload_hash):
            return {"skipped": True, "message": "이미 등록된 동일 자료입니다."}
        if file is not None and file.filename:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(data)
                tmp_path = tmp.name
        try:
            overview, body = build_notice_chunks(meta_obj, tmp_path, file.filename if tmp_path else None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            if tmp_path:
                os.unlink(tmp_path)

        to_docs = lambda cs: [Document(page_content=c["page_content"], metadata=c["metadata"]) for c in cs]
        if not meta and not body:
            raise HTTPException(status_code=422, detail="PDF에서 텍스트를 추출하지 못했습니다. OCR이 필요합니다.")
        docs = to_docs(overview + body)
        for doc in docs:
            doc.metadata["upload_hash"] = upload_hash
        keys = [overview[0]["metadata"]["source_file"]]
        if tmp_path:
            keys.append(f"{meta_obj['notice_id']}/{file.filename}")
        result = VECTORSTORE_WEB.add_documents(docs, source_files=keys)
        if result.get("skipped"):
            return result
        body_note = None
        if not body and tmp_path:
            body_note = "첨부 PDF에서 텍스트를 추출하지 못해 개요만 적재했습니다 (이미지 PDF면 OCR 필요)."
        m = overview[0]["metadata"]
        return {"message": f"공고 적재 완료: 개요 1개 + 본문 {len(body)}개 청크",
                "notice_id": m["notice_id"], "title": m["title"],
                "apply_start": m["apply_start"], "apply_end": m["apply_end"],
                "company_types": m["company_types"],
                "replaced_keys": [overview[0]["metadata"]["source_file"]] + ([body[0]["metadata"]["source_file"]] if body else []),
                "note": body_note}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 질의 요청/응답 모델 정의
# ✅ 질의 요청 모델
class RAGRequest(BaseModel):
    prompt: str = Field(default=..., description="질문 프롬프트")
    source: str = Field(default=..., description="web | pdf")
    top_k: int = Field(default=20, ge=1, le=20, description="검색할 결과 수 (기본값 20)")

    class Config:
        validate_by_name = True

# ✅ 응답 모델 (설명만 필요하면 Field 생략 가능)
class AnswerResponse(BaseModel):
    prompt: str
    response: str
    needs_review: bool = False
    review_reason: Optional[str] = None
    persona_applied: Optional[Dict[str, Any]] = None
    sources: List[Dict[str, Any]] = []
    retrieved: List[Dict[str, Any]] = []   # 검색 후보 (답을 못 찾아도 남김, 원인 분석용)
    config: Dict[str, Any] = {}            # 실험 설정 기록 (검색 방식·프롬프트·top_k)

# ✅ RAG 기반 질의 응답 처리
from fastapi import Body

# 유틸
def extract_by_marker(text: str,
                      start: str = "### ANSWER_START",
                      end: str = "### ANSWER_END") -> str | None:
    i = text.find(start)
    if i == -1:
        return None
    j = text.find(end, i + len(start))
    if j == -1:
        return None
    return text[i + len(start): j].strip()

# 컨텍스트가 "id:\n title:\n content:" 형태일 때의 간단 백업 파서(옵션)
import re
def fallback_extract_from_context(context: str, question: str) -> str | None:
    q = re.sub(r"[^\w가-힣A-Za-z0-9 ]", "", question).strip()
        # []: 문자 클래스 (이 안의 문자 중 하나와 매칭)
        # ^: 문자 클래스 앞에 있으면 "not"의 의미 → 이 안에 포함되지 않은 문자를 의미
        # \w: 알파벳 + 숫자 + 밑줄 → [A-Za-z0-9_]와 동일
        # 가-힣: 한글 완성형 유니코드 범위
        # A-Za-z0-9: 영문자와 숫자 (중복이긴 하지만 명시적 표현)
        # 공백( ): 띄어쓰기 허용
    blocks = re.split(r"\n\s*\n", context.strip())
    for b in blocks:
        m_title = re.search(r"^title:\s*(.+)$", b, flags=re.MULTILINE | re.IGNORECASE)
        m_content = re.search(r"^content:\s*(.+)$", b, flags=re.MULTILINE | re.IGNORECASE)
                    # ^content:
                    # → 줄의 처음이 content:로 시작해야 함
                    # (줄의 시작임을 의미하는 ^는 re.MULTILINE이 설정된 경우 각 줄마다 적용됨)
                    # \s*
                    # → content: 뒤에 공백이 0개 이상 있어도 허용
                    # (.+)
                    # → 공백을 제외한 내용이 최소 1자 이상 존재해야 함
                    # → 이 부분이 캡처 그룹 1번으로 추출됨
                    # $
                    # → 줄의 끝까지 매칭
        if m_title and m_content:
            t = re.sub(r"[^\w가-힣A-Za-z0-9 ]", "", m_title.group(1)).strip()
            if q.lower() in t.lower():
                return m_content.group(1).strip()
    m_any = re.search(r"^content:\s*(.+)$", context, flags=re.MULTILINE | re.IGNORECASE)
    return m_any.group(1).strip() if m_any else None

# ✅ 질의 응답 라우터
@app.post("/rag-query", response_model=AnswerResponse, summary="Closed Source LLM + 벡터 DB 질의", tags=["질의 응답"])
async def rag_query(
    rag_request: RAGRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    # 1. 벡터스토어 및 LLM 결정
    if rag_request.source == "web":
        if not (VECTORSTORE_WEB.info() or {}).get("vectors"):
            raise HTTPException(
                status_code=400,
                detail="❗ 외부 WEB 벡터값이 로딩되지 않았습니다. 먼저 외부 정보를 수집해서 벡터화를 진행해주세요.",
            )
        vectorstore = VECTORSTORE_WEB
        llm = ChatOpenAI(model="gpt-4o", temperature=LLM_TEMPERATURE, seed=LLM_SEED)
        llm_type = "openai" # huggingface

    elif rag_request.source == "pdf":
        if not (VECTORSTORE_PDF.info() or {}).get("vectors"):
            raise HTTPException(
                status_code=400,
                detail="❗ PDF 벡터값이 로딩되지 않았습니다. 먼저 PDF 파일을 벡터화해주세요.",
            )
        # [Q03] 요청 출처와 검색 저장소 연결
        # [설계 판단] 사용자가 source="pdf"를 선택하면 웹 저장소가 아니라 PDF 저장소를 검색하도록 연결하세요.
        # [TODO Q03] 아래 ... 부분을 적절한 코드로 교체하세요.
        vectorstore = VECTORSTORE_PDF
        llm = ChatOpenAI(model="gpt-4o", temperature=LLM_TEMPERATURE, seed=LLM_SEED)
        llm_type = "openai"

    else:
        raise HTTPException(
            status_code=400,
            detail="❗ 외부 source는 'web', 'pdf' 중 하나여야 합니다.",
        )

    # 2. 검색 및 LLM 응답 생성
    try:
        source = rag_request.source
        top_k = rag_request.top_k

        # -------------------- PDF: 검색 결과 확인 + 안정적인 응답 처리 --------------------
        if source == "pdf":
            # [Q04] 질문과 유사 문서 검색 연결
            # [설계 판단] 사용자 질문을 기준으로 선택된 벡터 저장소에서 상위 top_k개 문서와 거리 점수를 가져오세요.
            # [TODO Q04] 아래 ... 부분을 적절한 코드로 교체하세요.
            # 페르소나: feat-auth-api 머지 후 current_user["persona"] 로 교체
            persona = current_user.get("persona")

            # [Q04] 법령 검색 (law_pipeline 머지 후 search_law 내부만 hybrid로 교체)
            # 질문 재작성은 실험 결과 기각(RAG_QUERY_REWRITE=0) → pgvector + law_pipeline 검색만 사용
            rewritten = expand_query(rag_request.prompt) if USE_SYNONYMS else None
            if rewritten == rag_request.prompt:
                rewritten = None
            print(f"[검색어 확장] {rewritten!r}")
            retrieval = search_law_pipeline(vectorstore, rewritten or rag_request.prompt, top_k)
            docs_scores = [(Document(page_content=h["text"], metadata=h["metadata"]), h["score"])
                           for h in retrieval["hits"]]
            print(f"[PDF 검색] 질문={rag_request.prompt!r}, 검색 문서 수={len(docs_scores)}")
            for i, (doc, score) in enumerate(docs_scores, start=1):
                m = doc.metadata or {}
                print(f"[PDF 검색 {i}] 거리={score:.5f}, {m.get('law_name')} {m.get('article_no')}, 내용={doc.page_content[:120]!r}")

            needs_review, review_reason = judge_needs_review(rag_request.prompt, docs_scores)
            sources = retrieval["sources"]
            if retrieval["needs_review"]:
                needs_review = True
                review_reason = review_reason or "적용례·부칙 확인 필요"

            run_config = {"search": os.getenv("RAG_SEARCH_MODE", "hybrid"), "prompt": PROMPT_VERSION,
                          "top_k": top_k, "temperature": LLM_TEMPERATURE, "seed": LLM_SEED,
                          "query_rewrite": QUERY_REWRITE, "synonyms": USE_SYNONYMS, "rewritten": rewritten}
            retrieved = retrieved_list(docs_scores)

            context = build_law_context(docs_scores)
            supplementary = "\n\n".join(r["text"] for r in retrieval["supplementary_context"])
            if supplementary:
                context += "\n\n[관련 부칙·적용례]\n" + supplementary[:6000]
            if not context.strip():
                return AnswerResponse(prompt=rag_request.prompt, response=NOT_FOUND,
                                      needs_review=True, review_reason="검색 근거 없음",
                                      persona_applied=persona_applied(persona), sources=[],
                                      retrieved=retrieved, config=run_config)

            full_prompt = build_law_prompt(rag_request.prompt, context, persona)
            # [Q05] 검색 근거를 LLM 답변으로 연결
            raw = llm.invoke([HumanMessage(content=full_prompt)]).content
            raw = raw.strip() if isinstance(raw, str) else str(raw).strip()
            final = (extract_by_marker(raw) or raw).strip()

            if not final or final.upper() == "N/A" or NOT_FOUND in final:
                # 근거가 없다고 답했으면 출처를 붙이지 않는다 (근거 없는 출처 표시 방지)
                return AnswerResponse(prompt=rag_request.prompt, response=NOT_FOUND,
                                      needs_review=True, review_reason=review_reason or "근거에서 답을 찾지 못함",
                                      persona_applied=persona_applied(persona), sources=[],
                                      retrieved=retrieved, config=run_config)
            sources = cited_sources(sources, final)   # 답변에 인용된 조문만
            if needs_review:
                final = f"{final}\n\n{REVIEW_NOTICE}"
            return AnswerResponse(prompt=rag_request.prompt, response=final,
                                  needs_review=needs_review, review_reason=review_reason,
                                  persona_applied=persona_applied(persona), sources=sources,
                                  retrieved=retrieved, config=run_config)

        # -------------------- WEB 검색 --------------------
        elif source == "web":
            # 공고 검색: pgvector + BM25 hybrid (법령과 같은 방식), 페르소나 기업유형 일치 공고 우선
            persona = current_user.get("persona")
            rewritten = expand_query(rag_request.prompt) if USE_SYNONYMS else None
            if rewritten == rag_request.prompt:
                rewritten = None
            ranked = vectorstore.retrieve(rewritten or rag_request.prompt, top_k, hybrid=True)["ranked"]
            docs_scores = rank_notices_for_persona(ranked, persona)
            run_config = {"search": "hybrid", "collection": "web", "top_k": top_k,
                          "temperature": LLM_TEMPERATURE, "seed": LLM_SEED,
                          "synonyms": USE_SYNONYMS, "rewritten": rewritten}
            retrieved = [{"rank": i, "notice_id": (d.metadata or {}).get("notice_id"),
                          "title": (d.metadata or {}).get("title"), "page": (d.metadata or {}).get("page"),
                          "score": round(float(sc), 4)} for i, (d, sc) in enumerate(docs_scores, start=1)]

            context = build_notice_context(docs_scores, persona)
            if not context.strip():
                return AnswerResponse(prompt=rag_request.prompt, response=NOT_FOUND,
                                      persona_applied=persona_applied(persona), sources=[],
                                      retrieved=retrieved, config=run_config)

            raw = llm.invoke([HumanMessage(content=build_notice_prompt(rag_request.prompt, context, persona))]).content
            final = (raw.strip() if isinstance(raw, str) else str(raw).strip())
            if is_not_found(final):
                return AnswerResponse(prompt=rag_request.prompt, response=NOT_FOUND,
                                      persona_applied=persona_applied(persona), sources=[],
                                      retrieved=retrieved, config=run_config)
            return AnswerResponse(prompt=rag_request.prompt, response=final,
                                  persona_applied=persona_applied(persona),
                                  sources=notice_sources(docs_scores, final),
                                  retrieved=retrieved, config=run_config)

        # -------------------- 기타 --------------------
        else:
            return AnswerResponse(prompt=rag_request.prompt, response="지원하지 않는 소스입니다.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 실행 명령어 예시:
# python -m uvicorn rag_external-web_pdf_closed_llm:app --port 8001 --reload
