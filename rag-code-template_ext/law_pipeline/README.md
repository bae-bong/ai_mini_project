# 법령 PDF 데이터 파이프라인 — 윤도균 담당

법령 PDF 11개(노무 4개·기업 지원 7개)를 페이지별로 읽어 현재 조문과 시행 예정 조문을 구분하고, 출처 정보를 가진 청크를 OpenAI 임베딩과 FAISS에 적재합니다.
기준일은 `data/laws/sources.json`의 **2026-10-23**입니다. 원본은 수정하지 않습니다. 저장소 루트의 `upload-paper/`에는 PDF만 두며 코드·출처표·생성물은 이 폴더 밖에서 관리합니다.

`data/laws/raw/`의 기존 4개 사본은 보존되어 있지만 기본 실행에서는 사용하지 않습니다. 원본의 기준 경로는 `../upload-paper/`이며, 다른 경로는 `preprocess --raw-dir`로 명시할 수 있습니다.

## 빠른 실행

아래 명령은 저장소의 `rag-code-template_ext` 폴더에서 실행합니다. Python 3.11 이상을 사용하세요.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-law.txt

# API 키 없이 가능: 페이지 추출 → 조항 분리 → 날짜 분류 → 청크 생성
python -m law_pipeline preprocess

# OPENAI_API_KEY를 환경변수나 이 폴더의 .env에 설정한 뒤 실행
# 실제 OpenAI API 사용량이 발생합니다.
python -m law_pipeline build

# 부칙을 동반하는 근거 검색; GPT 답변 생성은 RAG 담당 영역
python -m law_pipeline search '연차 유급휴가의 발생 조건은 무엇인가요?' --k 5

# 검색 평가: 기대 조항이 상위 5개에 포함되는지 확인
python -m law_pipeline.evaluate

# 비교 실험: FAISS 의미 검색만 사용
python -m law_pipeline.evaluate --mode dense --output data/laws/processed/dense_evaluation.json

# API를 호출하지 않는 자동 테스트
python -m unittest discover -s law_pipeline/tests -v
```

이미 만들어진 인덱스가 있으면 `build`는 덮어쓰지 않고 중단합니다. 재생성이 필요할 때 `--replace`를 지정하면 기존 폴더를 `pdf.previous`로 보관하고 새 인덱스로 교체합니다. 백업이 이미 있으면 먼저 다른 곳에 보관하세요. 전처리 재실행만으로 인덱스가 바뀌지는 않습니다.

## 입력과 출력

| 경로 | 내용 |
|---|---|
| `../upload-paper/*.pdf` | 사용자 제공 원본 PDF 11개; 전처리의 기본 입력 경로 |
| `data/laws/sources.json` | 법령명·법률번호·시행일·기준일·원문 URL·원본 SHA-256 |
| `data/laws/processed/pages.jsonl` | 1부터 시작하는 PDF 페이지 번호와 추출 원문 |
| `data/laws/processed/articles.jsonl` | 현재·미래·검토 필요·부칙·제목·삭제 조문 전체 |
| `data/laws/processed/chunks.jsonl` | 현재 본문만 분할한 검색용 청크 |
| `data/laws/processed/supplements.jsonl` | 적용례·경과조치가 있는 부칙. 관련 법률 검색 시 동반 |
| `data/laws/processed/review_required.jsonl` | 부분 시행일·불명확한 중복 등 수동 검토 대상 |
| `data/laws/processed/report.json` | 페이지·조문·청크 개수 및 파일별 처리 현황 |
| `data/laws/processed/search_evaluation.json` | 질문 17개의 검색 결과·순위·출처 |
| `faiss_db/pdf/index.faiss` | 전체 11개 법령의 현행 청크 벡터 |
| `faiss_db/pdf/index.pkl` | LangChain 문서 및 메타데이터 |
| `faiss_db/pdf/chunks.jsonl` | 실제 적재 시각과 계약 필드를 포함한 최종 청크; FAISS 저장 문서와 동일 |
| `faiss_db/pdf/manifest.json` | 임베딩 모델·차원·기준일·원본 및 인덱스 해시 |
| `faiss_db/pdf/supplements.jsonl` | 인덱스와 같은 버전의 부칙 |

생성물은 Git에서 제외합니다. 인덱스가 없는 팀원은 원본·출처표·코드로 재생성할 수 있습니다.

## 처리 방식과 판단 이유

1. `pypdf`로 페이지별 텍스트를 추출합니다. 스캔 PDF나 텍스트가 없는 페이지는 조용히 건너뛰지 않고 실패합니다.
2. 법제처/국가법령정보센터 머리말과 페이지 반복 제목을 정리합니다. 원래 PDF 페이지 번호는 따로 보존합니다.
3. 줄 시작의 `제N조(제목)`, `제N조의N(제목)`, `제N조 삭제`를 조항 경계로 사용합니다. 본문 중 인용된 `제N조에 따라`는 경계가 아닙니다. 페이지를 넘어간 조항은 연결합니다.
4. 장·절 제목, 본문, 부칙을 구분합니다. 장 제목의 시행일이 직전 조문에 잘못 붙지 않게 합니다.
5. 조문 끝의 `[시행일: YYYY. M. D.]`를 그 조문 버전에 연결합니다. 기준일 이후에 전체 조문이 시행되면 `future`로 제외합니다.
6. 특정 항·호만 시행일이 다르면 자동으로 현재 문장을 복원하지 않습니다. `review_required`로 격리합니다. 같은 조항의 현행 후보가 여러 개여도 격리합니다.
7. 현행 조문 안에서 본문 최대 1,000자, 중첩 200자로 분할합니다. 가능한 줄 경계에서 자르며 각 청크 앞에 법령명·조항 제목을 붙입니다. 제목 접두사 길이는 본문 1,000자 한도에 포함되지 않습니다.
8. 11개 법령의 청크를 한꺼번에 적재하므로 마지막 PDF만 남는 덮어쓰기 문제를 피합니다.
9. 검색은 기본적으로 FAISS 의미 검색과 한글 2/3글자 묶음 BM25 검색의 순위를 RRF로 합칩니다. 법령의 정확한 용어가 의미 검색에서 빠지는 것을 줄이기 위한 방식입니다. `--mode dense`로 FAISS만 사용하는 결과와 비교할 수 있습니다. 현재 658개 청크를 대상으로 전체 거리와 키워드 점수를 계산하므로, 수십만 청크 규모라면 후보 검색 구조를 바꿔야 합니다.

`document_effective_date`는 문서 표제부의 시행일이지 개별 조항의 정확한 역사적 시행 시작일이 아닙니다. 현행 본문은 지정된 PDF 기준일의 상태로 다룹니다. 과거 질문이나 미래 기준일에 이 묶음을 재사용하지 말고 해당 시점 원문과 출처표를 갱신하세요. 날짜만 바꿔 미래 조항을 자동 활성화하지 않습니다.

### 실제 PDF에서 확인된 사례

- 근로기준법 제60조는 현재 버전과 2027-06-10 시행 버전이 함께 있습니다. 현재 버전은 PDF 14~15쪽에 걸쳐 있고, 같은 조항의 미래 버전은 15쪽에 있습니다.
- 근로기준법 제116조는 특정 항·호의 시행일이 섞여 있어 검토 목록으로 분리합니다. 해당 조문에 대한 검색 근거가 제한된다는 점을 RAG 담당자에게 알려야 합니다.
- 근로자퇴직급여 보장법 부칙에는 본문의 적용 인원을 시기별로 달리 정하는 내용이 있습니다. 따라서 부칙을 버리면 본문만으로 잘못 답할 수 있습니다. `search()`는 검색된 법률의 부칙을 `supplementary_context`로 함께 반환합니다.
- 중소기업창업 지원법 제29조에는 과거 시행일 표기가 남아 있습니다. 미래 조문으로 분류하지 않고, 적용 버전 확인이 필요한 검토 목록으로 보수적으로 분리했습니다.
- 부칙도 법적 적용을 자동 판정한 것이 아닙니다. 원문을 보존해 RAG에 전달하고 `needs_review`를 함께 제공합니다. 부칙에 나온 미래 날짜를 단순 삭제하면 적용 조건을 훼손할 수 있습니다.

## 메타데이터 계약

2026-10-23 팀 계약의 **법령(law) 부분**을 적용했습니다. 공고(notice) 수집·적재는 이 모듈의 범위 밖입니다.

| 계약 필드 | 저장 값 |
|---|---|
| `doc_type`, `doc_id` | `law`, `law:법령명` |
| `title`, `source_file`, `law_name` | 법령명, 원본 파일명, 법령명 |
| `page` | 해당 조문의 첫 PDF 페이지(1부터) |
| `chunk_index` | 원본 PDF 안에서 0부터 이어지는 청크 순번 |
| `ingested_at` | 인덱스 적재 시작 시각 ISO 8601, `+09:00` |
| `law_level` | 출처표에 명시한 법령 단계; 현재 11개 모두 `법률` |
| `category` | 출처표에 명시: 노무 4개는 `노무`, 기업 지원 7개는 `우대기업` |
| `article_no`, `article_title` | 조문 번호와 제목; 없으면 `null` |
| `effective_date` | PDF 표제부 시행일; 개별 항·호의 시행일과 구분 |

전처리 결과 `data/laws/processed/chunks.jsonl`은 적재 전 중간 자료이므로 `ingested_at`이 `null`입니다. `build`에서 실제 적재 시각을 채우고 필수 필드·타입·문서별 순번을 검증합니다. RAG에 전달할 최종 메타데이터는 **FAISS 문서 또는 `faiss_db/pdf/chunks.jsonl`**에서 읽으세요. 인덱스 생성에 실패한 경우 최종 파일은 교체되지 않습니다.

기존 `type`, `source`, `article`, `document_effective_date`도 호환용으로 보존합니다. 계약의 `chunk_index`는 문서별 순번으로 바꾸고, 기존 조문 안 순번은 `article_chunk_index`에 보존했습니다. `page`는 조문 첫 페이지이며, `page_start`, `page_end`, `pages`는 청크가 실제로 차지하는 페이지 범위입니다. 예를 들어 14~15쪽에 걸친 조문의 후반 청크는 `page=14`, `page_start=15`일 수 있습니다.

`law_id`, `source_url`, `pdf_sha256`, `record_id`, `chunk_id`, `as_of`, `status`, `requires_review` 등 추적·시행 구분 정보도 유지합니다. 법령의 `status=current`는 조문 분류이며, 공고의 접수 상태 저장 금지 규칙과는 별개입니다.

`law_pipeline.schema.law_sources(hits)`가 계약의 `{type, title, article, page, score}`로 변환합니다. `search()`에도 같은 `sources`를 추가했습니다. 별도 부칙인 `supplementary_context`는 본문 청크가 아니며, 기존 확장 형식으로 함께 전달해야 합니다.

## 이경민님에게 넘길 검색 연결

**기존 템플릿의 `OpenAIEmbeddings()` 기본값과 다른 모델로 만들었을 수 있으므로 아래 로더로 모델을 일치시키세요.** 차원이 같아도 모델이 다르면 검색이 틀어집니다.

```python
from pathlib import Path
from law_pipeline.index import load_index, search

index_dir = Path(__file__).resolve().parent / "faiss_db" / "pdf"
vectorstore = load_index(index_dir)  # manifest의 모델과 차원으로 로드

result = search(index_dir, "연차 유급휴가의 발생 조건은?", k=5, store=vectorstore)
# result["hits"]: 본문 검색 후보 [{text, metadata, score}, ...]
# result["sources"]: 계약 형식 [{type: "law", title, article, page, score}, ...]
# result["supplementary_context"]: 해당 법령의 부칙 [{text, metadata}, ...]
# result["as_of"]: 데이터 기준일
# result["needs_review"]: 부칙 적용 확인 필요 여부
```

본문 검색 후보와 부칙을 모두 LLM 문맥에 전달하세요. 문맥 길이 제한 때문에 적용 조건을 제공할 수 없으면 답변을 단정하지 않고 확인 필요로 처리해야 합니다. `score`는 제곱 L2 거리(낮을수록 가까움), `retrieval_score`는 두 검색 순위를 합친 RRF 값(높을수록 먼저)입니다. 기본 반환 순서는 RRF 기준이므로 L2 값이 오름차순일 필요는 없습니다. 두 값 모두 확률이나 법적 정확성이 아닙니다. 검색 결과가 있다는 것만으로 질문에 답할 근거가 있다는 뜻은 아닙니다.

이 모듈의 `needs_review`는 데이터 관점의 보수적인 표시입니다. 질문의 해고·분쟁 판단, 정보 부족 및 범위 밖 거절은 RAG/API 담당자가 추가합니다. 관련 조항에서 참조하는 다른 조항·시행령까지 자동으로 모두 따라가는 기능은 없습니다.

기존 서버의 `/upload-paper`는 일반 PDF용 경로입니다. 현재 파일 하나로 인덱스를 재생성하므로 이 법령 인덱스에 그대로 사용하지 마세요. 이 모듈의 전처리와 전체 재적재 경로를 연결해야 합니다. 법령 데이터 제공 범위는 여기까지입니다. 계약의 `POST /upload-paper(file, category, law_name)` 연결과 `/rag-query` 응답의 `sources=result["sources"]` 반영은 API/RAG 담당자가 수행해야 합니다. 현재 서버에는 이 연결을 적용하지 않았습니다.

인덱스는 LangChain `save_local()`과 호환되지만 `index.pkl`은 Python pickle입니다. 이 프로젝트에서 직접 만든 인덱스만 로드하세요. 해시 검증은 파일 혼합·손상을 찾기 위한 것으로 외부 인덱스의 안전성을 보증하지 않습니다.

## 데이터 범위와 남은 팀 작업

- 노무 4개·기업 지원 7개 법률을 포함합니다. 법률이 위임한 세부 요건, 확인서 유효기간, 공공구매 비율, 수습 감액 금액 등은 해당 시행령·시행규칙·고시를 추가 확인해야 합니다. 현재 자료에 없는 수치를 추측해서 답하지 않습니다.
- 여성 대표/1인 기업이라는 사용자 구분과 근로자에 대한 법 적용은 별개입니다. 질문에서 고용관계·사업장 규모 등 조건을 확인하는 로직이 필요합니다.
- 검색 평가는 예상 조항의 포함 여부입니다. 최종 답변 정확성, 출처 충실성, 모름 처리, 법적 적용 판단을 검증한 것은 아닙니다.
- RAG 담당자와 법령별 적용 조건·예외 질문을 포함한 평가셋을 확장하세요.

## 체크포인트에서 설명할 내용

“원문 PDF를 페이지별로 추출한 뒤 조항 경계로 연결했습니다. 미래 시행 조문은 기준일로 제외하고, 부분 시행은 자동 추측하지 않고 검토 목록에 남겼습니다. 조항 안에서 청킹해 페이지와 출처를 보존했고, 부칙은 본문 검색과 함께 전달합니다. 원본 해시와 임베딩 모델을 기록해 같은 데이터로 재실행할 수 있습니다.”
