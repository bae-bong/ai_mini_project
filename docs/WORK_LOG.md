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

## 2026-10-23 — EC2 기동 확인 및 로컬 빌드 방식 보류

- 사용자가 제공한 EC2 로그에서 auth/rag 이미지 빌드 완료와 nginx/db/auth/rag 컨테이너의 실행을 확인했다. db/nginx/rag는 `docker compose ps`에서 healthy로 표시됐다. auth는 별도 healthcheck 없이 Up 상태다.
- 제공된 출력에는 `/api/rag/health` 응답 본문이 없어 Nginx를 통한 RAG 호출 성공은 추가 확인이 필요하다. 실제 사용자 질의와 데이터 적재도 아직 검증되지 않았다.
- 로컬 ARM64 이미지 빌드 → save/gzip → scp → EC2 load → `up --no-build` 절차를 `deploy/LOCAL_IMAGE_DEPLOY.md`에 저장했다. 사용자 요청에 따라 적용은 보류하고 EC2 빌드 방식을 유지한다.
- 이번 작업은 문서 기록만 수행하며 이미지 전송·서비스 변경·커밋·push는 하지 않았다.

## 2026-10-23 — 필수 프론트엔드 4개 페이지

### 변경

- `frontend/index.html`, `assets/app.js`, `assets/styles.css`: 회원가입·로그인·AI 상담·자료 관리 화면을 하나의 Vue 앱으로 구성했다. Nginx가 `/signup`, `/login`, `/chat`, `/documents`에서 제공한다.
- 화면 분야 명칭은 경영지원/사업공고이며 내부 source 값은 기존 pdf/web을 유지한다. 별도 소개 페이지·상담 기록·공고 상세·기업 정보 수정 페이지는 추가하지 않았다.
- 기존 가입·로그인·로그아웃 API와 쿠키 인증을 연결했다. 가입 시 기업 유형 복수 선택을 쉼표로 묶어 기존 서버에 전달한다. 기업 정보는 RAG의 JWT 검증 결과에서 읽는다.
- 상담에 분야별 자료 준비 상태·제출 중 표시·답변·출처·기업 정보·전문가 확인 안내·오류를 구현했다. 자료 미적재 상태에서는 제출을 막는다.
- 자료 관리에는 준비 상태·PDF 형식/20MB 제한·업로드 결과·관리 컬렉션의 409 안내를 구현했다. 미구현 목록·삭제·사업공고 등록을 동작하는 기능처럼 표시하지 않는다.
- Vue 3.5.13 런타임과 MIT 라이선스를 포함해 CDN 없이 실행한다. 별도 프론트 서버나 이미지 빌드는 없다.
- Compose에 프론트 마운트를 추가하고 LOGIN_REDIRECT를 /chat으로 변경했다. Nginx에 네 화면과 assets 경로를 추가하고 상대 리다이렉트로 SSH 터널 포트를 유지한다. 기존 개발 콘솔·백엔드 경로는 유지한다.

### 검증

- `node --check frontend/assets/app.js`, `docker compose config --quiet`, `git diff --check`: 통과.
- `python3 deploy/smoke_test.py`: 새 정적 경로·자체 Vue 런타임·로그인 이동 경로를 포함한 Compose 검증과 기존 16개 테스트 통과. 외부 AI API를 호출하지 않았다.
- ego-browser에서 실제 임시 PostgreSQL 계정 가입·비밀번호 확인·로그인·기업 유형 두 개/직원 수 표시·로그아웃·미인증 페이지 이동을 확인했다.
- 브라우저 내 테스트 응답으로 답변·출처·전문가 확인 표시, javascript URL 차단, 잘못된 파일 형식 차단, 관리 자료 409 안내, 사업공고 준비 상태, 키보드 탭 이동을 확인했다.
- 390px 너비에서 DOM 기준 가로 넘침이 없음을 확인했다. 브라우저 스크린샷 도구가 타임아웃되어 이미지 기반 시각 검증은 완료하지 못했다.
- 실제 EC2·운영 데이터는 변경하지 않았다. 실행·반영 절차는 `frontend/README.md`에 기록했다. 커밋·push는 하지 않았다.

### 후속

- EC2에서 최신 코드 반영 후 Nginx와 auth만 재생성해 새 마운트·로그인 경로를 적용한다. UI 변경 자체는 백엔드 이미지 재빌드가 필요 없다.
- 실제 자료 적재 후 AI 답변 품질 검증, 관리자 권한, 사업공고 등록·목록/삭제 등 백엔드 기능은 별도 작업이다.

## 2026-10-23 — EC2 운영 절차 문서화

- `deploy/README.md`에 EC2 시작·상태 검사·현재 주소 확인, Mac의 SSH 터널, EC2의 GitHub clone/pull, 서버 .env 작성, Compose 실행·확인, 브라우저 접속, 법령 적재를 순서대로 작성했다.
- 최초 배포와 기존 서버 업데이트를 구분했다. UI만 변경한 경우와 백엔드도 변경한 경우의 실행 명령을 나누고 Nginx 설정 갱신 방법을 포함했다.
- EC2 중지 후 재시작, 서비스만 중지·재개, 자주 발생한 GitHub 인증·502·404·터널 포트 오류를 기록했다. Mac과 EC2에서 실행할 명령을 명시했다.
- 같은 문서에 Docker 공식 저장소를 통한 최초 설치와 기존 DB 갱신·이관·백업 절차를 통합했다. 루트 README는 기존 프로젝트 개요와 배포 문서 링크로 유지했다. 현재 정적 프론트 경로 설명도 바로잡았다.
- 문서의 셸 코드 블록 구문과 상대 파일 링크를 검사했다. 문서 수정만 수행했으며 EC2 접속·서비스 변경·비밀값 조회·커밋·push는 하지 않았다.

## 2026-10-23 — DuckDNS 도메인 공개 HTTPS 설정 준비

- 사용자 도메인 `skala-ai.duckdns.org`와 기존 EC2 퍼블릭 DNS를 조회해 둘 다 `32.236.191.133`을 가리키는 것을 확인했다. DNS 레코드를 변경하거나 DuckDNS 토큰을 사용하지 않았다.
- `compose.public.yaml`을 추가했다. 기본 SSH용 Compose는 유지하고 공개 모드에서 Nginx 80/443과 인증서·ACME 디렉터리만 추가한다. DB·auth·rag 포트는 비공개다.
- 기존 Nginx 앱 경로를 `app.conf`로 분리했다. 인증서 발급 전 HTTP 설정은 ACME 파일만 제공하고 일반 요청은 503으로 제한한다. 최종 설정은 HTTPS에서 앱을 제공하고 HTTP는 HTTPS로 이동시킨다. HTTPS 인증 쿠키에 Secure를 추가한다.
- `deploy/README.md`에 보안 그룹·DNS 확인·Certbot 발급·HTTPS 전환·자동 갱신 hook·공개 모드 재배포·SSH 모드 복귀 절차를 기록했다.
- `python3 deploy/test_https.py` 통과: 임시 자체서명 인증서로 ACME bootstrap, HTTP 308, HTTPS 인증서 검증·네 페이지, Secure 쿠키, Nginx 재로드를 확인했다. 실제 인증서는 발급하지 않았다.
- `python3 deploy/smoke_test.py`도 통과해 공통 Nginx 경로 분리 후 기존 비공개 Compose·인증·RAG와 16개 회귀 테스트가 유지됨을 확인했다.
- 공개 Compose 구성·문서 셸 문법·링크·Python 문법·diff 공백 검사 통과. 실제 EC2·보안 그룹·인증서 발급·갱신 timer는 사용자가 문서 절차로 적용하고 확인해야 한다. 커밋·push는 하지 않았다.
