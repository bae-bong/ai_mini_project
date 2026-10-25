"""No paid API calls or Whisper download; ffmpeg decoding uses a real WAV."""
import asyncio
import io
import os
import sys
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient
from jose import jwt

from app import voice_api
from app.services import stt_service, tts_service


def token(**claims):
    return jwt.encode({"sub": "42", "username": "voice-user", "iss": "simple-auth-server",
                       "exp": int(time.time()) + 60, **claims}, "test-voice-secret", algorithm="HS256")


def wav_bytes(seconds=0.1):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * int(seconds * 16000))
    return output.getvalue()


class VoiceAPITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"JWT_SECRET": "test-voice-secret", "OPENAI_API_KEY": "test-only"})
        self.env.start()
        self.client = TestClient(voice_api.app)
        self.headers = {"Authorization": "Bearer " + token()}

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def upload(self, data=b"audio", content_type="audio/webm", **kwargs):
        return self.client.post("/stt/", headers=self.headers,
                                files={"audio": ("../../same.webm", data, content_type)}, **kwargs)

    def test_both_paid_and_cpu_endpoints_require_login(self):
        with patch.object(voice_api, "transcribe_upload") as stt, patch.object(tts_service, "synthesize_speech") as tts:
            self.assertEqual(self.client.post("/stt/", files={"audio": ("a.webm", b"a", "audio/webm")}).status_code, 401)
            self.assertEqual(self.client.post("/tts/", json={"text": "answer"}).status_code, 401)
            stt.assert_not_called()
            tts.assert_not_called()

    def test_invalid_expired_wrong_secret_and_wrong_issuer_are_rejected(self):
        bad_tokens = ["invalid", token(exp=1), token(iss="other"), jwt.encode(
            {"sub": "42", "username": "user", "iss": "simple-auth-server"}, "other", algorithm="HS256")]
        for value in bad_tokens:
            with self.subTest(value=value):
                response = self.client.post("/tts/", headers={"Authorization": "Bearer " + value}, json={"text": "answer"})
                self.assertEqual(response.status_code, 401)

    def test_java_cookie_is_accepted_and_temporary_paths_are_cleaned(self):
        paths = []
        def transcribe(path):
            paths.append(path)
            self.assertEqual(path.name, "recording.audio")
            self.assertEqual(path.read_bytes(), b"audio")
            return "우리 회사 지원사업을 찾아주세요"
        with patch.object(voice_api, "transcribe_upload", side_effect=transcribe):
            self.client.cookies.set("ACCESS_TOKEN", token())
            response = self.client.post("/stt/", files={"audio": ("../../same.webm", b"audio", "audio/webm;codecs=opus")})
        self.assertEqual(response.status_code, 200)
        self.assertIn("지원사업", response.json()["text"])
        self.assertFalse(paths[0].parent.exists())

    def test_cleanup_and_error_status_on_transcription_failure(self):
        paths = []
        def fail(path):
            paths.append(path)
            raise RuntimeError("private provider details")
        with patch.object(voice_api, "transcribe_upload", side_effect=fail):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.assertFalse(paths[0].parent.exists())

    def test_invalid_type_empty_oversize_and_silence_do_not_search(self):
        with patch.object(voice_api, "transcribe_upload", return_value="") as transcribe:
            self.assertEqual(self.upload(content_type="application/pdf").status_code, 415)
            self.assertEqual(self.upload(data=b"").status_code, 422)
            with patch.object(voice_api, "MAX_AUDIO_BYTES", 5):
                self.assertEqual(self.upload(data=b"123456").status_code, 413)
            transcribe.assert_not_called()
            self.assertEqual(self.upload().status_code, 422)

    def test_real_audio_decode_before_mocked_whisper(self):
        with patch.object(stt_service, "transcribe_audio_file_local", return_value="청년 채용 지원사업") as transcribe:
            response = self.upload(data=wav_bytes(), content_type="audio/wav")
            wav_path = Path(transcribe.call_args.args[0])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(wav_path.exists())

    def test_overlong_and_malformed_audio_never_reach_whisper(self):
        with patch.object(stt_service, "transcribe_audio_file_local") as transcribe:
            with patch.object(voice_api, "MAX_AUDIO_SECONDS", 1):
                self.assertEqual(self.upload(data=wav_bytes(1.5), content_type="audio/wav").status_code, 422)
            self.assertEqual(self.upload(data=b"not audio").status_code, 422)
            transcribe.assert_not_called()

    def test_speech_returns_private_audio_without_creating_files(self):
        with patch.object(tts_service, "synthesize_speech", return_value=b"ID3-test") as synthesize:
            response = self.client.post("/tts/", headers=self.headers, json={"text": "  기존 RAG 답변  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ID3-test")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        synthesize.assert_called_once_with("기존 RAG 답변")
        self.assertEqual(self.client.get("/uploads/anything.mp3").status_code, 404)

    def test_bad_speech_input_and_provider_failure(self):
        with patch.object(tts_service, "synthesize_speech", side_effect=RuntimeError("private")) as synthesize:
            for text in ["", "   ", "a" * 4097]:
                self.assertEqual(self.client.post("/tts/", headers=self.headers, json={"text": text}).status_code, 422)
            synthesize.assert_not_called()
            response = self.client.post("/tts/", headers=self.headers, json={"text": "valid"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private", response.text)

    def test_speech_provider_has_timeout_and_legacy_file_api_still_works(self):
        response = MagicMock(status_code=200, content=b"ID3-audio")
        response.__enter__.return_value = response
        with patch.object(tts_service.requests, "post", return_value=response) as post:
            self.assertEqual(tts_service.synthesize_speech("안내"), b"ID3-audio")
        self.assertEqual(post.call_args.kwargs["timeout"], (5, 60))
        self.assertEqual(post.call_args.kwargs["json"]["response_format"], "mp3")
        response.status_code = 429
        with patch.object(tts_service.requests, "post", return_value=response):
            with self.assertRaises(RuntimeError):
                tts_service.synthesize_speech("안내")


class NonblockingVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_responds_while_inference_is_running(self):
        started, release = threading.Event(), threading.Event()
        def slow_transcription(path):
            started.set()
            release.wait(5)
            return "질문"
        with patch.dict(os.environ, {"JWT_SECRET": "test-voice-secret"}), patch.object(voice_api, "transcribe_upload", side_effect=slow_transcription):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=voice_api.app), base_url="http://test") as client:
                request = asyncio.create_task(client.post("/stt/", headers={"Authorization": "Bearer " + token()},
                                                         files={"audio": ("a.webm", b"audio", "audio/webm")}))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    health = await asyncio.wait_for(client.get("/health"), timeout=1)
                    self.assertEqual(health.status_code, 200)
                finally:
                    release.set()
                    self.assertEqual((await request).status_code, 200)


if __name__ == "__main__":
    unittest.main()
