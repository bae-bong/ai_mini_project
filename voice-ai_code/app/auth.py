"""Share the Java login cookie with the voice API without another login."""
import os

from fastapi import Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    access_token: str | None = Cookie(default=None, alias="ACCESS_TOKEN"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    token = access_token or (credentials.credentials if credentials else None)
    if not token:
        raise HTTPException(401, "로그인이 필요합니다.")
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise HTTPException(503, "음성 서비스 인증 설정을 확인해 주세요.")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], issuer="simple-auth-server")
        if payload.get("sub") is None or payload.get("username") is None:
            raise JWTError("Missing user claims")
    except JWTError:
        raise HTTPException(401, "로그인이 만료되었거나 유효하지 않습니다.") from None
    return payload
