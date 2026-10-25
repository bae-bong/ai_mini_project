import os
import requests
from dotenv import load_dotenv

# Load .env (even if already loaded in main.py, duplicate calls are harmless)
load_dotenv()

def synthesize_speech(text: str) -> bytes:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    if not text.strip() or len(text) > 4096:
        raise ValueError("Speech input must contain 1 to 4096 characters")
    url = "https://api.openai.com/v1/audio/speech"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    data = {
        "model": os.getenv("VOICE_TTS_MODEL", "tts-1"),
        "input": text,
        "voice": os.getenv("VOICE_TTS_VOICE", "nova"),
        "response_format": "mp3",
    }

    with requests.post(url, headers=headers, json=data, timeout=(5, 60)) as response:
        if response.status_code != 200:
            raise RuntimeError(f"TTS provider returned HTTP {response.status_code}")
        if not response.content:
            raise RuntimeError("TTS provider returned empty audio")
        return response.content


def text_to_speech(text: str, output_path: str = "output.mp3"):
    audio = synthesize_speech(text)
    with open(output_path, "wb") as f:
        f.write(audio)

    return output_path
