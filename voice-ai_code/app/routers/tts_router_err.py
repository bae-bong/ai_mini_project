# NOTE: This router is legacy test code no longer used in the current main.py and is currently not in use

from fastapi import APIRouter, HTTPException, Form
from app.services.tts_service import text_to_speech
import os

router = APIRouter()

# Path to the uploads folder for saving output files
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/")
async def tts_endpoint(text: str = Form(...)):
    try:
        output_path = os.path.join(UPLOAD_DIR, "openai_tts_output.mp3")
        file_path = text_to_speech(text, output_path)
        return {
            "message": "TTS processing complete (OpenAI TTS)",
            "file_path": file_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))