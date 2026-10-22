import os
import requests
from dotenv import load_dotenv

# Load .env (even if already loaded in main.py, duplicate calls are harmless)
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set. Please check your .env file or environment variables.")


def text_to_speech(text: str, output_path: str = "output.mp3"):
    url = "https://api.openai.com/v1/audio/speech"
    
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "tts-1",
        "input": text,
        "voice": "nova"
    }

    response = requests.post(url, headers=headers, json=data)
    print(f"TTS response status code: {response.status_code}")

    if response.status_code != 200:
        raise Exception(f"TTS request failed: {response.status_code} - {response.text}")

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path