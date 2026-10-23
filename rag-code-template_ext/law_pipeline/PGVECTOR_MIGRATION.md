# PostgreSQL/pgvector 전환 (2026-10-23)

현재 실행 경로는 PostgreSQL/pgvector다. 이 디렉터리의 기존 README·VALIDATION·REVIEW는 팀원이 수행한 FAISS 구현과 평가의 역사적 기록으로 보존한다. 과거 수치가 pgvector 전환 후 재측정되었다는 의미는 아니다.

## 보존한 기능

- PDF 페이지 추출·조 단위 청킹·미래 조문 및 검토 대상 분리
- 법령 메타데이터 계약, 모델·차원·기준일·원본 출처
- BM25/RRF 결합 검색, 부칙·적용례·출처 반환, 평가 질문
- 기존 HTTP API 경로·JWT·기업 페르소나

## 변경한 인터페이스

- `law_pipeline build`: 파일 인덱스 대신 DB의 pdf 컬렉션에 원자적 적재
- `law_pipeline search`, `evaluate`: DB에서 검색. `--index` 옵션 제거
- `load_index()`: DB 모델 정보를 읽어 동일 임베딩 설정 사용
- `rag-query`: 파이프라인의 hybrid 검색과 관련 부칙을 실제 API에서도 사용
- 직접 PDF 업로드: 파일별 교체, 다른 파일 보존, 관리 컬렉션 혼합 금지

운영 의존성에서 FAISS·MySQL 연결 패키지를 제거했다. 기존 FAISS의 벡터는 일회성 `law_pipeline.migrate_faiss` 명령으로 복사할 수 있다. 지원 범위와 명령은 [배포 문서](../../deploy/README.md)에 있다.

임베딩 모델·차원 불일치는 거절한다. pgvector의 L2 거리를 제곱해 기존 squared L2 기준을 유지한다. exact 검색을 사용하며 HNSW는 아직 사용하지 않는다. 실제 API·임베딩 품질·EC2 성능은 별도 실측해야 한다.
