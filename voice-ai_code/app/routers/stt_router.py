from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, status, Cookie
from jose import jwt, JWTError
import shutil
import os

# Replace with local Whisper STT function (transcribe_audio_file_local)
from app.services.stt_service import transcribe_audio_file_local, convert_webm_to_wav

router = APIRouter()

# Configuration that must match the Java server
SECRET_KEY = "RANDOM_SECRET_KEY"   # Must be identical to Java JwtUtil.SECRET
ALGORITHM = "HS256"
ISSUER = "simple-auth-server"         # Must be identical to Java JwtUtil.ISSUER

def get_current_user(
    access_token: str | None = Cookie(default=None, alias="ACCESS_TOKEN")
):
    """
    Reads the ACCESS_TOKEN(JWT) cookie issued by the Java server on port 8080
    and returns the current logged-in user information as a dependency.
    """
    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (no ACCESS_TOKEN cookie)",
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
            detail="Invalid or expired token",
        )

    user_id = payload.get("sub")
    username = payload.get("username")

    if user_id is None or username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # If needed, query the DB here and return an actual User object
    return {"id": user_id, "username": username}

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/")
async def stt(
    current_user: dict = Depends(get_current_user),
    audio: UploadFile = File(...)):
    webm_path = os.path.join(UPLOAD_DIR, audio.filename)

    with open(webm_path, "wb") as buffer:
        shutil.copyfileobj(audio.file, buffer)

    try:
        # Convert webm → wav
        wav_path = webm_path.replace(".webm", ".wav")
        convert_webm_to_wav(webm_path, wav_path)

        # Whisper STT (using local version)
        text = transcribe_audio_file_local(wav_path)

        os.remove(webm_path)
        os.remove(wav_path)
        return {
            "text": text,
            "user": current_user  # For debugging: verify user information
        }
    except Exception as e:
        return {"error": str(e)}
