"""
JWT 검증 의존성 (Java 인증 서버 :8080 이 발급한 토큰을 검증)

- 토큰 위치: ACCESS_TOKEN 쿠키 또는 Authorization: Bearer <token> 헤더
- 비밀키/알고리즘/발급자는 Java JwtUtil 과 반드시 같아야 함
- 사용법: async def endpoint(user: dict = Depends(get_current_user))
"""
import os

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

JWT_ALGORITHM = "HS256"
# Swagger(/docs) 의 Authorize 버튼용. 헤더가 없어도 바로 401 내지 않고 쿠키를 확인하도록 auto_error=False
bearer_scheme = HTTPBearer(auto_error=False, description="로그인 응답의 accessToken")
JWT_ISSUER = "simple-auth-server"             # Java JwtUtil.ISSUER


def _get_secret() -> str:
    # import 시점이 아니라 요청 시점에 읽는다 (.env 로드 순서 문제 방지)
    secret = os.getenv("JWT_SECRET")          # Java 쪽 export JWT_SECRET 과 같은 값
    if not secret:
        raise RuntimeError("JWT_SECRET 환경변수가 없습니다. .env 를 확인하세요.")
    return secret


def _extract_token(cookie_token: str | None,
                   credentials: HTTPAuthorizationCredentials | None) -> str | None:
    """쿠키 우선, 없으면 Bearer 헤더에서 꺼낸다."""
    if cookie_token:
        return cookie_token
    if credentials and credentials.credentials:
        return credentials.credentials.strip() or None
    return None


def get_current_user(
    access_token: str | None = Cookie(default=None, alias="ACCESS_TOKEN"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    token = _extract_token(access_token, credentials)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (no token)",
        )

    try:
        payload = jwt.decode(
            token,
            _get_secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id = payload.get("sub")
    username = payload.get("username")
    if user_id is None or username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # 페르소나 claim (로그인 때 Java 가 넣어줌). 없으면 미입력으로 처리
    employees = payload.get("employees")
    company_types = payload.get("companyTypes") or []

    return {
        "id": int(user_id),
        "username": username,
        "persona": {
            "employees": employees if isinstance(employees, int) else None,
            "company_types": [t for t in company_types if isinstance(t, str) and t],
        },
    }
