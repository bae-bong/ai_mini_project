# 프로젝트 작업 기록

팀에 영향을 주는 변경을 날짜별로 남긴다. 각 기록은 목적, 변경 범위, 검증, 남은 작업을 포함하며 실행 방법은 deploy/README.md를 기준으로 관리한다.

## 2026-10-23 — PostgreSQL/pgvector 및 배포 통합

### 목적

회원 데이터와 벡터 데이터를 PostgreSQL로 통합하고, 팀원이 개발한 인증·법령 처리 기능을 단일 EC2용 Compose에 연결한다.

### 변경 범위

- `compose.yaml`: auth/rag 연결, PostgreSQL 초기 스키마 마운트, 모델·JWT 환경변수, 원본 PDF와 전처리 저장소
- `core/pgvector.sql`, `core/vector_store.py`: 컬렉션·청크·벡터·모델·부칙 저장, 트랜잭션 교체, 모델·차원 검증
- `law_pipeline/index.py`, CLI·evaluate: FAISS 파일 대신 DB 적재·검색, 기존 청킹·BM25/RRF·출처·부칙 유지
- `law_pipeline/migrate_faiss.py`: 신뢰하는 기존 IndexFlatL2의 벡터를 API 호출 없이 복사
- RAG API: pgvector 검색, 실제 JWT 페르소나 및 관련 부칙 반영
- `workplace/sql/alter_users.sql`: PostgreSQL 문법으로 수정
- Nginx·기존 콘솔: 인증 폼 경로 전달과 동일 출처 RAG API 기본 주소
- 운영 requirements: FAISS/MySQL 저장 의존성 제거, psycopg 추가. FAISS는 별도 이관 requirements로 분리
- 배포 가이드·파이프라인 변경 안내·통합 테스트 추가/갱신

### 검증

- `python3 deploy/smoke_test.py`: 통과. ARM64 Docker에서 Nginx/auth/rag/db 이미지 빌드·기동, 실제 회원가입·로그인, Java JWT의 RAG 검증, DB 재생성 후 데이터 보존을 확인했다.
- 같은 명령 안에서 기존 법령 파이프라인 테스트 13개, API/저장소 통합 테스트 2개, 별도 임시 컨테이너의 FAISS 이관 테스트 1개가 통과했다. 기본 운영 이미지에는 FAISS가 없어 이관 테스트가 한 번 skip되며, 별도 이관 컨테이너에서 실제 실행했다.
- 이관 테스트는 기존 벡터의 squared L2 점수·메타데이터 일치와 원본 파일 불변을 확인했다. 실제 팀 데이터의 검색 품질을 재평가한 결과는 아니다.
- `docker compose config --quiet`, Python AST 문법 검사, `git diff --check`: 통과.
- 임베딩·LLM에는 테스트 대역을 사용했다. 외부 AI API·실제 EC2·운영 DB는 사용하지 않았고 테스트 컨테이너와 volume은 종료 후 정리했다.
- `.gitignore`의 기존 사용자 수정은 유지했다. 커밋·push는 하지 않았다.

### 남은 작업

- 실제 기존 FAISS 인덱스·회원 데이터 이관과 백업 복원 확인
- 담당자가 동일 평가셋으로 실제 임베딩·답변 품질 재측정
- EC2에서 기동·자원 사용 확인 및 도메인·TLS 공개 배포
- 기업마당 공고 적재, 음성 RAG 등 기능 작업은 해당 담당자 범위로 유지

과거 팀원 FAISS 평가 문서는 당시 결과로 보존한다. 임베딩 모델 변경이나 실제 데이터 재적재는 이번 코드 변경과 별개다.
