# EC2 배포

단일 EC2에서 Nginx, Java 인증 서버, FastAPI RAG, PostgreSQL 17 + pgvector를 Docker Compose로 실행한다. S3와 음성 서비스는 이번 구성에 포함하지 않는다. 인증 서버는 현재 JDK HttpServer이며 Spring Boot로 바꾸지 않았다.

## 실행

저장소 루트에서 Docker Engine과 Compose 플러그인을 사용한다.

```sh
cp .env.example .env
```

`.env`에 `POSTGRES_PASSWORD`, `JWT_SECRET`, `OPENAI_API_KEY`를 입력한다. 비밀번호와 JWT 비밀값은 `openssl rand -hex 32` 등으로 각각 생성한다. `.env`는 커밋하지 않는다. `EMBEDDING_MODEL`은 기존 모델인 `text-embedding-ada-002`가 기본이며, 적재와 검색에 동일한 모델을 사용한다. 축소 차원을 지원하는 모델에서만 `EMBEDDING_DIMENSIONS`를 설정한다.

```sh
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/api/rag/health
```

- `http://127.0.0.1:8080/signup`에서 가입하고 `/login`에서 로그인한다.
- 로그인 후 `/external_index.html`로 이동한다. 기본 API 주소는 같은 출처의 `/api/rag`다. 브라우저에 예전 API 주소가 저장되어 있으면 화면에서 전체 주소 `http://127.0.0.1:8080/api/rag`로 변경한다.
- 네이버 `/upload-topic`은 기존 결정대로 비활성화 상태다. 콘솔의 해당 버튼은 아직 남아 있다.
- DB와 백엔드는 호스트 포트를 공개하지 않는다. 기본 Nginx 바인딩도 `127.0.0.1:8080`이다.

EC2 실행 시 로컬 컴퓨터의 다른 터미널에서 접속한다.

```sh
ssh -i /path/to/testkey.pem -L 18080:127.0.0.1:8080 ubuntu@EC2_HOST
```

로컬 브라우저에서 `http://127.0.0.1:18080/login`을 연다. 보안 그룹에 8080·5432·8001을 열 필요는 없다. 공개 배포는 도메인과 TLS를 구성한 뒤 Nginx의 바인딩·80/443 구성을 변경한다. SSH는 관리자 IP로 제한한다.

## DB 초기화와 기존 volume 갱신

빈 PostgreSQL volume에는 아래 SQL이 파일명 순서로 자동 적용된다.

1. `deploy/postgres/001-vector.sql`: vector 확장
2. `workplace/sql/init.sql`: 회원·기업 페르소나
3. `rag-code-template_ext/core/pgvector.sql`: 컬렉션·청크·벡터·메타데이터

기존 DB volume은 초기화 SQL을 다시 실행하지 않는다. 먼저 백업한 뒤 필요한 마이그레이션을 적용한다. 아래는 기존 volume에 이번 스키마를 추가하는 명령이다. `init.sql`은 이미 존재하는 테이블을 변경하지 않으므로, 기존 users의 페르소나 컬럼은 `alter_users.sql`도 적용한다.

```sh
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < workplace/sql/init.sql
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < workplace/sql/alter_users.sql
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < rag-code-template_ext/core/pgvector.sql
```

이 명령은 MariaDB 사용자 데이터를 자동 이관하지 않는다. 기존 DB의 버전·스키마·데이터는 별도로 확인해 옮긴다. `.env`의 비밀번호 변경만으로 기존 PostgreSQL 계정 비밀번호가 바뀌지는 않는다.

## 법령 적재와 검색

원본 PDF는 `upload-paper/`를 읽기 전용으로 마운트한다. 전처리 결과는 `law_processed` volume, 임베딩·메타데이터·모델·부칙은 PostgreSQL의 `rag_collections`, `rag_chunks`에 함께 저장한다. 실행에 FAISS 파일이나 pickle이 필요하지 않다.

```sh
docker compose exec rag python -m law_pipeline preprocess
docker compose exec rag python -m law_pipeline build
docker compose exec rag python -m law_pipeline search '연차 유급휴가 발생 조건은?'
docker compose exec rag python -m law_pipeline.evaluate
```

`build`는 OpenAI 임베딩 API를 호출한다. 다른 모델을 사용한다면 `--model` 및 필요 시 `--dimensions`를 `.env` 설정과 맞춘다. 기존 pdf 컬렉션을 재생성할 때는 백업 후 `build --replace`를 사용한다. 전체 청크·임베딩·모델·부칙을 하나의 DB 트랜잭션으로 교체하며, 실패하면 기존 데이터가 유지된다.

- 기존 청킹·미래 조문 제외·메타데이터 계약·BM25/RRF 검색을 유지한다.
- pgvector L2 거리를 제곱하여 기존 FAISS squared L2 점수 의미를 유지한다. 점수는 법적 확신도가 아니다.
- 현재 규모에서는 정확 검색을 사용한다. HNSW 등 근사 인덱스는 추가하지 않았다.
- 직접 `/upload-paper` 적재는 같은 파일명을 교체하고 다른 파일은 유지한다. 파이프라인이 관리하는 컬렉션에는 직접 업로드할 수 없다.
- 예전 CLI의 `--index` 옵션은 제거했다. DB 컬렉션이 저장·검색의 기준이다.

## 기존 FAISS 자료 이관

원본 FAISS 디렉터리를 별도 보관한다. 다음 명령은 신뢰하는 로컬 파일만 읽고, 기존 임베딩을 재사용해 PostgreSQL에 복사한다. 원본 파일은 삭제·수정하지 않는다. pickle을 읽으므로 외부에서 받은 미검증 파일에는 사용하지 않는다.

```sh
docker compose run --rm --no-deps \
  -v "$PWD/rag-code-template_ext/faiss_db:/legacy:ro" \
  rag sh -c 'pip install -r requirements-migration.txt && python -m law_pipeline.migrate_faiss /legacy/pdf --trusted-local-files'
```

- `manifest.json`이 없으면 실제 기존 모델을 `--model`로 명시한다.
- 비어 있지 않은 `IndexFlatL2`만 지원한다. 기존 DB 컬렉션을 덮어쓰려면 백업 후 `--replace`를 명시한다.
- 법령 출처 메타데이터가 없는 오래된 인덱스는 자동 보충하지 않고 이관을 거절한다. 원본 PDF를 재적재한다.
- FAISS 의존성은 일회성 이관 컨테이너에만 설치하며, 운영 RAG 이미지에는 포함하지 않는다.
- 이관 후 `search`와 동일 평가셋을 비교하고 모델·차원·개수를 확인한다. 실제 임베딩 기반 품질 평가는 담당자가 실행해야 한다.

## 경로와 연결 규격

| 외부 경로          | 내부 경로                  |
| ------------------ | -------------------------- |
| `/api/auth/signup` | `auth:8080/api/signup`     |
| `/api/auth/login`  | `auth:8080/api/login`      |
| `/api/auth/logout` | `auth:8080/api/logout`     |
| `/api/auth/check`  | `auth:8080/api/auth/check` |
| `/api/rag/*`       | `rag:8001/*` (접두사 제거) |

기존 인증 HTML의 `/login`, `/signup`, `/static/*`, `/api/login` 등도 Nginx가 전달한다. Java는 `DB_URL`, `DB_USER`, `DB_PASSWORD`, `JWT_SECRET`, `LOGIN_REDIRECT`를 받는다. RAG는 `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`와 동일한 `JWT_SECRET`을 받는다. 로컬 개발에서는 표준 PostgreSQL URI인 `RAG_DATABASE_URL`도 지원한다. DB 주소는 컨테이너 안에서 `localhost` 대신 `db`를 사용한다.

## 테스트

```sh
python3 deploy/smoke_test.py
```

별도 무작위 이름의 테스트 스택을 생성한다. 실제 인증·JWT 전달, pgvector 검색·모델·차원 검사, 교체 실패 롤백, 메타데이터·부칙·페르소나 응답, 기존 법령 PDF 회귀 검사, 컨테이너 재생성 후 데이터 보존을 검증한다. 임베딩과 LLM은 테스트 대역을 사용해 외부 AI API를 호출하지 않는다. 종료 시 해당 테스트 컨테이너·volume만 제거한다.

`RUN_DB_TESTS=1`은 폐기 가능한 DB에서만 사용한다. 통합 테스트는 테스트 컬렉션을 지운다. 일반 법령 파서 테스트는 DB 없이 실행할 수 있다.

```sh
cd rag-code-template_ext
python -m unittest discover -s law_pipeline/tests -v
```

## 백업·복구

```sh
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/postgres-$(date +%Y%m%d-%H%M%S).dump"
```

종료 코드 0을 확인하고 사본을 EC2 밖에도 보관한다. 이 dump는 회원·청크·벡터·모델 설정·부칙을 함께 포함한다. 복원 검증은 별도 임시 DB에서 수행한다. 원본 PDF도 별도 보관한다. `docker compose down`은 named volume을 유지하고, `down -v`는 DB와 전처리 volume을 삭제하므로 운영에서 사용하지 않는다.

## 작업 기록

변경 이유·영향 파일·검증 결과·남은 작업은 [docs/WORK_LOG.md](../docs/WORK_LOG.md)에 날짜별로 기록한다. 팀의 과거 평가 결과와 검토 문서는 당시 FAISS 결과로 남기고, 이번 변경의 안내는 [PGVECTOR_MIGRATION.md](../rag-code-template_ext/law_pipeline/PGVECTOR_MIGRATION.md)를 따른다.
