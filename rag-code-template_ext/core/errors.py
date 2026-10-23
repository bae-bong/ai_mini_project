"""
공통 오류 처리 - 모든 오류 응답을 {"detail": "..."} 한 가지 형식으로 통일

사용법 (main.py):
    from app.core.errors import register_error_handlers
    register_error_handlers(app)
"""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("api")


def register_error_handlers(app: FastAPI) -> None:

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # 필수값 누락, 타입 오류 등 → 400
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid input: {field} - {first.get('msg', '')}"},
        )

    @app.exception_handler(Exception)
    async def unknown_error(request: Request, exc: Exception):
        # LLM·임베딩·DB 실패 등 → 500, 원인은 로그에만 남김
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
