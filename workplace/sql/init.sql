-- PostgreSQL 초기 스키마 (인증 서버 users 테이블 + pgvector 확장)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
  id             BIGSERIAL    PRIMARY KEY,
  username       VARCHAR(100) NOT NULL UNIQUE,
  password       VARCHAR(255) NOT NULL,
  company_types  VARCHAR(200) NULL,
  employees      INT          NULL,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
