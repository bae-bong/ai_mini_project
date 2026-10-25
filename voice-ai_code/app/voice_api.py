"""Voice input/output for the existing RAG chat. No public audio files."""
import asyncio
import logging
import subprocess
import tempfile
import wave
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user

# Load configuration before any provider modules. Compose injects these directly.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

app = FastAPI(title="RAG voice input and output")
logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_AUDIO_SECONDS = 60
AUDIO_TYPES = {"audio/webm", "video/webm", "audio/ogg", "audio/mp4", "video/mp4", "audio/wav", "audio/x-wav"}
# CPU inference and speech generation each have a bounded queue per worker.
stt_slot = asyncio.Semaphore(1)
tts_slot = asyncio.Semaphore(2)


def transcribe_upload(path: Path) -> str:
    # Import lazily: /health and TTS do not need to download a Whisper model.
    from app.services.stt_service import convert_webm_to_wav, transcribe_audio_file_local

    wav_path = path.with_suffix(".wav")
    try:
        convert_webm_to_wav(str(path), str(wav_path), max_seconds=MAX_AUDIO_SECONDS + 1)
    except (RuntimeError, subprocess.TimeoutExpired):
        raise HTTPException(422, "음성 파일을 읽지 못했어요. 다시 녹음해 주세요.") from None
    with wave.open(str(wav_path), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    if duration > MAX_AUDIO_SECONDS:
        raise HTTPException(422, "음성은 60초 이내로 녹음해 주세요.")
    return transcribe_audio_file_local(str(wav_path)).strip()


@app.get("/health")
def health():
    # Liveness only; it does not assert model availability or call paid APIs.
    return {"status": "ok"}


@app.post("/stt/")
async def transcribe(
    audio: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    try:
        content_type = (audio.content_type or "").split(";", 1)[0].lower()
        if content_type not in AUDIO_TYPES:
            raise HTTPException(415, "WebM, Ogg, MP4 또는 WAV 음성을 사용해 주세요.")
        # Never use the client filename as a filesystem path.
        with tempfile.TemporaryDirectory(prefix="rag-voice-") as directory:
            path = Path(directory) / "recording.audio"
            size = 0
            with path.open("wb") as output:
                while chunk := await audio.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise HTTPException(413, "음성 파일은 12MB 이하여야 합니다.")
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "녹음된 음성이 없습니다.")
            async with stt_slot:
                text = await run_in_threadpool(transcribe_upload, path)
        if not text:
            raise HTTPException(422, "음성을 인식하지 못했어요. 다시 녹음해 주세요.")
        if len(text) > 4000:
            raise HTTPException(422, "질문을 더 짧게 녹음해 주세요.")
        return {"text": text}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Voice transcription failed")
        raise HTTPException(503, "음성을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.") from None
    finally:
        await audio.close()


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


@app.post("/tts/")
async def speak(body: SpeechRequest, user: dict = Depends(get_current_user)):
    from app.services.tts_service import synthesize_speech

    text = body.text.strip()
    if not text:
        raise HTTPException(422, "읽어줄 답변이 없습니다.")
    try:
        async with tts_slot:
            audio = await run_in_threadpool(synthesize_speech, text)
    except Exception:
        logger.exception("Voice synthesis failed")
        raise HTTPException(503, "답변 음성을 만들지 못했어요. 텍스트 답변을 확인해 주세요.") from None
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
