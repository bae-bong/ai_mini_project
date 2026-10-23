-- users 테이블에 페르소나 컬럼 추가 (기업 유형·직원 수)
-- 실행: docker exec -i mariadb mariadb -u root -p1234 sql_db < alter_users.sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS company_types VARCHAR(200) NULL COMMENT '쉼표 구분 기업 유형',
  ADD COLUMN IF NOT EXISTS employees     INT          NULL COMMENT '상시근로자 수';
