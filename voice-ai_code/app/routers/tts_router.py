from fastapi import APIRouter, Form, Depends, HTTPException, Cookie
from jose import jwt, JWTError
from app.services.tts_service import text_to_speech
import os

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
            status_code=401,
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
            status_code=401,
            detail="Invalid or expired token",
        )

    user_id = payload.get("sub")
    username = payload.get("username")

    if user_id is None or username is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token payload",
        )

    # If needed, query the DB here and return an actual User object
    return {"id": user_id, "username": username}


# Path to the uploads folder for saving output files
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/")
async def tts_endpoint(
    current_user: dict = Depends(get_current_user),   # Authentication added
    text: str = Form(...)
):
    try:
        file_name = "openai_tts_output.mp3"
        output_path = os.path.join(UPLOAD_DIR, file_name)

        # Actual TTS generation processing
        file_path = text_to_speech(text, output_path)  # Absolute path for internal storage

        # Return web path for client
        return {
            "message": "TTS processing complete (OpenAI TTS)",
            "file_path": f"/uploads/{file_name}",  # Return web path only
            "user": current_user  # For debugging: verify user information
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))