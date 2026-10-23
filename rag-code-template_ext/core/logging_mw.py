"""
요청 로그 미들웨어 - 요청 ID, 경로, 상태코드, 응답시간을 한 줄로 남김
질문 원문은 남기지 않음 (개인정보). 필요하면 mask_pii() 로 가린 뒤 남긴다.

사용법 (main.py):
    from app.core.logging_mw import add_logging_middleware
    add_logging_middleware(app)
"""
import logging
import re
import time
import uuid

from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("api")

_PHONE = re.compile(r"01[0-9]-?\d{3,4}-?\d{4}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def mask_pii(text: str) -> str:
    """전화번호·이메일 마스킹 (질문을 로그에 남겨야 할 때만 사용)"""
    text = _PHONE.sub("***-****-****", text)
    return _EMAIL.sub("***@***", text)


def add_logging_middleware(app: FastAPI) -> None:

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = uuid.uuid4().hex[:8]
        request.state.request_id = request_id
        start = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "req=%s %s %s status=%s %dms",
            request_id, request.method, request.url.path, response.status_code, elapsed_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
