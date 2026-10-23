-- users 테이블에 페르소나 컬럼 추가 (기업 유형·직원 수)
-- PostgreSQL의 기존 users 테이블에 적용한다. 신규 DB는 init.sql을 사용한다.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS company_types VARCHAR(200) NULL,
  ADD COLUMN IF NOT EXISTS employees INT NULL;
COMMENT ON COLUMN users.company_types IS '쉼표 구분 기업 유형';
COMMENT ON COLUMN users.employees IS '상시근로자 수';
