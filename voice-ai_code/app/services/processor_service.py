from openai import OpenAI
from dotenv import load_dotenv
import os
import logging

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


SUPPORTED_TARGET_LANGUAGES = {
    "en": "English",
    "zh": "Chinese",
    "ko": "Korean",
    "ja": "Japanese",
    "vi": "Vietnamese",
}


def get_llm_response(user_input: str, target_language: str = "en") -> str:
    """
    Translate STT text into the selected target language.
    This function does NOT answer the user's message.
    It only translates the recognized speech for TTS output.
    """

    if not user_input or not user_input.strip():
        logger.warning("[LLM] Empty input detected")
        return "I'm sorry, I couldn't understand the audio clearly. Could you please try again?"

    user_input = user_input.strip()

    if target_language not in SUPPORTED_TARGET_LANGUAGES:
        logger.warning(f"[LLM] Unsupported target language: {target_language}")
        target_language = "en"

    target_language_name = SUPPORTED_TARGET_LANGUAGES[target_language]

    logger.info(f"[LLM TRANSLATION INPUT] {user_input}")
    logger.info(f"[LLM TARGET LANGUAGE] {target_language_name}")

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional speech translation engine.\n"
                        "\n"
                        "Task:\n"
                        f"Translate the user's spoken text into natural {target_language_name}.\n"
                        "\n"
                        "Rules:\n"
                        "1. Do NOT answer the user's message.\n"
                        "2. Do NOT continue the conversation.\n"
                        "3. Do NOT add explanations, greetings, comments, or extra context.\n"
                        "4. Do NOT mention the source language.\n"
                        "5. Preserve the original meaning and tone as much as possible.\n"
                        "6. If the input is already in the target language, lightly polish it without changing the meaning.\n"
                        "7. Return only the translated sentence or paragraph.\n"
                    ),
                },
                {
                    "role": "user",
                    "content": user_input,
                },
            ],
            temperature=0.1,
        )

        translated_text = response.choices[0].message.content.strip()

        logger.info(f"[LLM TRANSLATION OUTPUT] {translated_text}")

        return translated_text

    except Exception as e:
        logger.exception(f"[LLM ERROR] {e}")
        return "I'm sorry, something went wrong while translating the text."