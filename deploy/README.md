# EC2 배포 준비

현재 기본 구성은 Nginx와 PostgreSQL 17 + pgvector다. 인증·RAG 애플리케이션 코드는 변경하지 않았고, 각 디렉터리에 이미지 빌드용 Dockerfile만 추가했다. S3와 음성 서비스는 포함하지 않는다.

## 현재 단계

- Nginx는 기존 소개 화면과 외부 RAG 콘솔을 제공한다.
- DB는 호스트 포트를 공개하지 않고 named volume에 저장한다.
- 기존 인증 코드는 MariaDB를 사용하므로 아직 Compose에 연결하지 않는다.
- 기존 RAG는 FAISS를 사용한다. pgvector 컨테이너 기동이 애플리케이션 전환 완료를 의미하지 않는다.
- `/api/auth/*`, `/api/rag/*`는 향후 `auth:8080`, `rag:8001`로 전달한다. 백엔드 연결 전에는 502가 정상이다.
- 기존 콘솔의 API BASE_URL은 `/api/rag`로 설정해야 한다. 콘솔의 버튼과 로그인 흐름은 팀원 코드 통합 후 확인한다.

## 로컬 또는 EC2에서 기동

저장소 루트에서 실행한다. Docker Engine과 Docker Compose 플러그인이 필요하다.

```sh
cp .env.example .env
```

`.env`의 `POSTGRES_PASSWORD`에 긴 임의 값을 입력한다. 예: `openssl rand -hex 24`로 생성한다. 실제 `.env`는 커밋하지 않는다.

```sh
docker compose config --quiet
docker compose up -d --wait
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
docker compose exec -T nginx nginx -t
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extversion FROM pg_extension WHERE extname = '\''vector'\'';"'
```

현재 바인딩은 `127.0.0.1:8080`이다. EC2에서 확인할 때 로컬 컴퓨터의 다른 터미널에서 SSH 터널을 연다.

```sh
ssh -i /path/to/testkey.pem -L 18080:127.0.0.1:8080 ubuntu@EC2_HOST
```

로컬 브라우저에서 `http://127.0.0.1:18080`을 연다. EC2 보안 그룹에 8080이나 5432를 추가할 필요는 없다.

배포 설정 전체 점검은 다음 명령으로 실행한다. 무작위 이름의 별도 테스트 프로젝트에서 정적 화면, pgvector 연산, DB 컨테이너 재생성 후 데이터 보존, 모의 백엔드로 프록시 경로·쿼리·본문 전달을 확인한다. 종료 시 해당 테스트 컨테이너와 테스트 volume만 삭제한다. 실제 인증·RAG 기능 검증은 포함하지 않는다.

```sh
python3 deploy/smoke_test.py
```

## 백엔드 합류 시 연결 규격

| 서비스 | 내부 주소 | 확인할 계약 |
|---|---|---|
| auth | `auth:8080` | PostgreSQL JDBC 환경변수, 마이그레이션 실행, JWT_SECRET, 로그인 후 상대 경로 이동 |
| rag | `rag:8001` | PostgreSQL 연결 환경변수·드라이버, pgvector 모델·차원, JWT 검증, `/health` |
| db | `db:5432` | 현재 기본 DB명·계정은 `.env` 기준 |

Dockerfile 빌드는 다음과 같다. 인증 이미지는 기존 JDK HttpServer용이며 Spring Boot 전환 후 실행 산출물에 맞춰 갱신한다.

```sh
docker build -t ai-mini-auth:local workplace
docker build -t ai-mini-rag:local rag-code-template_ext
```

통합할 때 auth/rag 서비스를 루트 Compose에 추가하고 DB의 `service_healthy` 조건을 사용한다. PostgreSQL 연결 주소의 호스트는 `localhost`가 아닌 `db`다. 아직 백엔드가 읽지 않는 환경변수 이름이나 테이블 스키마를 배포 코드에서 가정하지 않는다.

| 외부 경로 | 내부 경로 |
|---|---|
| `/api/auth/signup` | `/api/signup` |
| `/api/auth/login` | `/api/login` |
| `/api/auth/logout` | `/api/logout` |
| `/api/auth/check` | `/api/auth/check` |
| `/api/rag/rag-query` | `/rag-query` |
| `/api/rag/upload-paper` | `/upload-paper` |
| `/api/rag/health` | `/health` |

공개 배포는 인증 없는 RAG 업로드·질의 경로가 보호되는 것을 확인하고 도메인·TLS를 구성한 다음 진행한다. 그때 Nginx의 바인딩과 80/443 포트 구성을 변경한다. SSH 22는 관리자의 IP로 제한한다.

## 데이터 보존과 백업

`001-vector.sql`은 빈 DB 초기화 때만 실행된다. 기존 DB에 적용할 때는 동일 SQL을 명시적으로 실행한다. 팀원이 이미 구축한 DB의 버전·스키마·데이터는 자동으로 가져오지 않으며, 합류 시 마이그레이션 또는 dump/restore 방식으로 이관한다.

```sh
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/postgres-$(date +%Y%m%d-%H%M%S).dump"
```

백업 명령의 종료 코드가 0인지 확인하고 사본을 EC2 밖에도 보관한다. 복원 검증은 운영 DB가 아닌 별도 임시 DB에서 한다. `docker compose down`은 named volume을 유지한다. `docker compose down -v`는 DB volume을 삭제하므로 사용하지 않는다. 초기화 후 `.env`의 비밀번호를 바꾸는 것만으로 기존 DB 비밀번호가 변경되지는 않는다.
