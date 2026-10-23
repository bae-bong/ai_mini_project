# 인증·API 실행 가이드 (feat-auth-api)

## 구성
| 서버 | 포트 | 역할 |
|---|---|---|
| PostgreSQL + pgvector | 5432 | 회원 DB (`users`) |
| Java 인증 서버 | 8080 | 회원가입·로그인·JWT 발급 |
| RAG 서버 (FastAPI) | 8001 | JWT 검증 후 검색·답변 |

## 1. DB
```bash
docker run -d --name pgvector -p 5432:5432 \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=1234 -e POSTGRES_DB=sql_db \
  pgvector/pgvector:pg16
sleep 10
docker exec -i pgvector psql -U postgres -d sql_db < workplace/sql/init.sql
```
맥에 PostgreSQL이 따로 깔려 있으면 5432가 충돌합니다: `brew services stop postgresql`

## 2. Java 인증 서버
```bash
cd workplace
export DB_URL='jdbc:postgresql://localhost:5432/sql_db'
export DB_USER='postgres'
export DB_PASSWORD='1234'
export JWT_SECRET='RANDOM_SECRET_KEY'
./gradlew run
```
필수 환경변수(`JWT_SECRET`, `DB_PASSWORD`)가 없으면 시작 시 바로 종료됩니다.

## 3. RAG 서버
```bash
cd rag-code-template_ext
cp .env.example .env    # OPENAI_API_KEY 채우기
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-e.txt
python -m uvicorn rag_external-web_pdf_closed_llm:app --port 8001 --reload
```

## 4. 동작 확인
```bash
# 회원가입 (302)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/api/signup \
  --data-urlencode "username=check01" --data-urlencode "password=1234" \
  --data-urlencode "employees=3" --data-urlencode "companyTypes=여성기업"

# 로그인 → 토큰
TOKEN=$(curl -s -X POST http://localhost:8080/api/login -d "username=check01&password=1234" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")

# 토큰 없음 → 401
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/me

# 페르소나 확인 → 200
curl -s http://localhost:8001/me -H "Authorization: Bearer $TOKEN"
```
Swagger: `http://localhost:8001/docs` → Authorize → `accessToken` 붙여넣기

## 인증 규칙
- 토큰 위치: `ACCESS_TOKEN` 쿠키 또는 `Authorization: Bearer <token>`
- 검증: HS256 서명, 발급자 `simple-auth-server`, 만료 1시간
- 실패 시 401 `{"detail": "..."}`
- RAG 안에서 사용: `current_user["persona"]` → `{"employees": 3, "company_types": ["여성기업"]}`

## 로그
- 요청마다 `req=<id> METHOD path status=<code> <ms>ms`, 응답 헤더 `X-Request-ID`
- 주민·사업자번호·전화번호·이메일은 로그에 남기 전 자동 마스킹
