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

_RRN = re.compile(r"\b\d{6}-?[1-4]\d{6}\b")              # 주민등록번호
_BIZ = re.compile(r"\b\d{3}-?\d{2}-?\d{5}\b")             # 사업자등록번호
_PHONE = re.compile(r"\b01[0-9]-?\d{3,4}-?\d{4}\b")        # 휴대폰
_TEL = re.compile(r"\b0\d{1,2}-\d{3,4}-\d{4}\b")          # 유선전화
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def mask_pii(text: str) -> str:
    """주민번호·사업자번호·전화번호·이메일 마스킹"""
    text = _RRN.sub("******-*******", text)
    text = _BIZ.sub("***-**-*****", text)
    text = _PHONE.sub("***-****-****", text)
    text = _TEL.sub("***-****-****", text)
    return _EMAIL.sub("***@***", text)


class PIIMaskingFilter(logging.Filter):
    """모든 로그 메시지를 남기기 전에 개인정보를 가린다"""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = mask_pii(msg)
        record.args = ()
        return True


def install_pii_filter() -> None:
    """루트 로거와 uvicorn 로거의 모든 핸들러에 마스킹 필터를 건다"""
    f = PIIMaskingFilter()
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "api"):
        lg = logging.getLogger(name)
        lg.addFilter(f)
        for h in lg.handlers:
            h.addFilter(f)


def add_logging_middleware(app: FastAPI) -> None:
    install_pii_filter()

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
