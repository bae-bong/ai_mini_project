from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status, Cookie
from jose import jwt, JWTError
import os
import shutil
import logging
import uuid

# Local STT / LLM / TTS services
from app.services.stt_service import transcribe_audio_file_local, convert_webm_to_wav
from app.services.processor_service import get_llm_response
from app.services.tts_service import text_to_speech

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration that must match the Java server
SECRET_KEY = "RANDOM_SECRET_KEY"
ALGORITHM = "HS256"
ISSUER = "simple-auth-server"

SUPPORTED_TARGET_LANGUAGES = {"en", "zh", "ko", "ja", "vi"}


def get_current_user(
    access_token: str | None = Cookie(default=None, alias="ACCESS_TOKEN")
):
    """
    Reads the ACCESS_TOKEN(JWT) cookie issued by the Java server
    and returns the current logged-in user information.
    """

    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. ACCESS_TOKEN cookie is missing.",
        )

    try:
        payload = jwt.decode(
            access_token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )

    user_id = payload.get("sub")
    username = payload.get("username")

    if user_id is None or username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload.",
        )

    return {
        "id": user_id,
        "username": username,
    }


# Upload directory configuration
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/")
async def stt_to_tts(
    current_user: dict = Depends(get_current_user),
    audio: UploadFile = File(...),
    target_language: str = Form("en"),
):
    """
    Full pipeline:
    Audio file → STT → selected target language translation → TTS → MP3 URL
    """

    try:
        logger.info("STT → LLM → TTS request started")

        # 1. Validate target language
        if target_language not in SUPPORTED_TARGET_LANGUAGES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported target language. Supported values are: en, zh, ko, ja, vi.",
            )

        # 2. Validate uploaded file
        if audio.filename is None or audio.filename.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No audio file was uploaded.",
            )

        original_filename = audio.filename
        file_ext = os.path.splitext(original_filename)[1].lower()

        if file_ext != ".webm":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .webm audio files are supported.",
            )

        # 3. Create unique file names to avoid overwriting files
        request_id = str(uuid.uuid4())
        webm_path = os.path.join(UPLOAD_DIR, f"{request_id}.webm")
        wav_path = os.path.join(UPLOAD_DIR, f"{request_id}.wav")
        tts_output_path = os.path.join(UPLOAD_DIR, f"{request_id}_response.mp3")

        logger.info(f"Target language: {target_language}")
        logger.info(f"Uploaded file path: {webm_path}")

        # 4. Save uploaded file
        with open(webm_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        logger.info("Audio file saved successfully")

        # 5. Convert webm → wav
        convert_webm_to_wav(webm_path, wav_path)
        logger.info(f"webm → wav conversion complete: {wav_path}")

        # 6. STT processing
        # [Q03] 음성 인식 서비스 연결
        # 힌트: 변환된 WAV 파일 경로를 로컬 STT 서비스에 전달하여 텍스트를 얻으세요.
        # TODO Q03: None을 올바른 서비스 호출로 교체하세요.
        stt_text = transcribe_audio_file_local(wav_path)
        logger.info(f"STT conversion result: {stt_text}")

        # 7. LLM translation processing
        # [Q04] STT 결과와 번역 서비스 연결
        # 힌트: 인식된 문장과 사용자가 선택한 목표 언어를 함께 전달하세요.
        # TODO Q04: None을 번역 서비스 호출로 교체하세요.
        llm_response = get_llm_response(stt_text, target_language)
        logger.info(f"LLM translated text: {llm_response}")

        # 8. TTS processing
        # [Q05] 번역 텍스트와 음성 합성 서비스 연결
        # 힌트: 번역된 문장과 저장할 MP3 파일 경로를 함께 전달하세요.
        # TODO Q05: pass를 올바른 TTS 서비스 호출로 교체하세요.
        pass
        text_to_speech(llm_response, tts_output_path)

        # 9. Return as HTTP URL
        tts_file_url = f"/uploads/{request_id}_response.mp3"
        logger.info(f"TTS file path returned to client: {tts_file_url}")

        return {
            "message": "STT → LLM → TTS processing complete",
            "stt_text": stt_text,
            "llm_response": llm_response,
            "target_language": target_language,
            "tts_file_path": tts_file_url,
            "user": current_user,
        }

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(f"STT → LLM → TTS pipeline failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred during STT → LLM → TTS processing.",
        )