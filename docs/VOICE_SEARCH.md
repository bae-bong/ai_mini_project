# 기존 RAG에 음성 입력·답변 듣기 연결

## 구현 판단

별도의 검색 엔진을 만들 필요 없이 기존 `/api/rag/rag-query`를 재사용한다.
음성 인식 결과를 사용자가 수정한 후 제출하므로 기업명·법령명 등의 오인식이 바로 검색에 반영되지 않는다.
로그인 쿠키, 분야 선택, 기업 정보, 답변 출처, 전문가 검토 안내는 기존 검색 흐름을 따른다.

1. `/chat`에서 경영지원 또는 사업공고를 선택한다.
2. **음성으로 질문**을 누르고 최대 60초 동안 녹음한다.
3. **녹음 완료**를 누르면 `/api/voice/stt/`가 로컬 Whisper로 텍스트를 만든다.
4. 질문란의 문장을 확인·수정하고 **답변 받기**를 누른다.
5. 기존 RAG API가 검색한 답변과 출처를 표시한다.
6. 필요할 때 **답변 듣기**를 눌러 `/api/voice/tts/`로 음성을 생성한다.

음성 인식 직후 자동 검색하거나 자동 번역하지 않는다. TTS는 추가 요약·답변 생성 없이 표시된 답변을 읽으며, 검토가 필요한 답변에는 전문가 확인 안내를 덧붙인다. 사업공고 검색은 DB에 적재한 공고를 대상으로 하므로 실시간 인터넷 검색·현재 접수 여부 검증을 새로 제공하지 않는다.

## 구성과 API

통합 배포는 `voice-ai_code/app/voice_api.py`를 별도 서비스로 실행한다.
기존 `app.main:app` 통번역 데모의 페이지·라우터는 유지하며, 통합 배포에서 공개하지 않는다.
검색 서비스의 코드나 임베딩을 음성 서버에 복사하지 않는다.

| 경로 | 요청 | 응답 |
|---|---|---|
| `POST /api/voice/stt/` | multipart `audio` | `{ "text": "인식한 질문" }` |
| `POST /api/voice/tts/` | JSON `{ "text": "표시된 답변" }` | `audio/mpeg`, `Cache-Control: no-store` |
| `GET /api/voice/health` | 없음 | 프로세스 생존 상태. 모델·외부 API 준비 상태 보장은 아님 |
| `POST /api/rag/rag-query` | 기존 `prompt`, `source`, `top_k` | 기존 답변·출처·검토 여부 |

STT와 TTS는 Java/RAG와 같은 `JWT_SECRET`으로 `ACCESS_TOKEN` 쿠키 또는 Bearer 토큰을 검증한다.
Nginx가 같은 도메인의 `/api/voice/`를 내부 `voice:8008`로 전달한다. 호스트에 음성 서버 포트를 추가로 열지 않는다.

STT는 WebM·Ogg·MP4·WAV, 최대 12MiB·60초를 허용한다. 클라이언트 파일명을 경로에 사용하지 않으며, 요청별 임시 폴더와 변환 WAV는 성공·실패 후 정리한다. ffmpeg 변환은 실제 오디오 내용을 검증하며 30초 제한을 둔다. CPU 추론은 한 번에 하나, TTS는 두 개까지 worker thread에서 실행한다. 취소·화면 이탈 시 마이크와 브라우저 요청을 해제하고 늦게 도착한 결과는 버린다. 이미 시작한 서버 추론이나 외부 TTS 요청 자체는 끝날 수 있다.

TTS는 기존 Speech API를 재사용한다. 4,096자를 넘는 답변은 잘라 읽지 않고 텍스트로 안내한다. 생성한 음성은 서버 파일로 저장하지 않고 응답 본문으로 반환하며 브라우저는 해당 답변의 재생에 재사용한다. 새 검색·분야 변경·로그아웃·페이지 이탈 시 Blob URL을 해제한다. AI 생성 음성임을 화면에 표시한다.

## 실행

루트 `.env`의 기존 `JWT_SECRET`, `OPENAI_API_KEY`를 사용한다. 선택 설정은 `.env.example`을 따른다.

```sh
docker compose up -d --build voice
docker compose up -d --no-deps --force-recreate nginx
```

`/chat`에서 기능을 이용한다. 마이크 입력은 HTTPS 또는 localhost의 지원 브라우저가 필요하다. 일반 HTTP의 서버 IP에서는 텍스트 입력을 사용할 수 있다.

첫 STT 요청은 `WHISPER_MODEL`(기본 `small`)을 내려받아 CPU에 로드하므로 느릴 수 있다. 모델 캐시는 `whisper_models` 볼륨에 유지한다. 배포 후 첫 사용 전에 모델 다운로드·로딩을 확인하려면 다음을 실행한다.

```sh
docker compose exec voice python -c 'from app.services.stt_service import get_model; get_model(); print("Whisper ready")'
```

CPU·메모리 여유와 실제 한국어 인식 품질·응답시간은 배포 환경에서 확인한다. 모델은 요청 시 로드되므로 `/health` 성공만으로 다운로드·추론 성공을 판단하지 않는다.

현재 기본 TTS는 기존 `tts-1`/`nova`이며 `VOICE_TTS_MODEL`, `VOICE_TTS_VOICE`로 설정한다. OpenAI 공식 문서에 따르면 기존 Speech API 모델은 2027-01-06 종료 예정이며 Realtime API로의 워크플로 전환이 필요하다. 새로운 Realtime 모델명만 Speech API 설정에 넣어서는 동작하지 않는다. [Speech API와 전환 안내](https://developers.openai.com/api/docs/guides/text-to-speech), [입력 길이 제한](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create).

## 검증

Python 테스트 환경에는 `fastapi`, `httpx`, `python-jose[cryptography]`, `python-multipart`, `requests`, `python-dotenv`가 필요하며 `ffmpeg`가 PATH에 있어야 한다. Whisper 설치·다운로드와 실제 AI 호출은 테스트에서 대체한다.

```sh
python -m unittest discover -s voice-ai_code/tests -v
node --test frontend/tests/voice.test.cjs
node --check frontend/assets/app.js
node --check frontend/assets/voice.js
git diff --check
```

API 테스트: 쿠키/Bearer 인증, 잘못된 JWT, 임시 파일 정리, 형식·크기·길이·무음, 실제 WAV 변환, TTS 입력 제한·실패·비공개 응답, 추론 중 health 응답.
브라우저 로직 테스트: 녹음→질문 반영, MP4 선택, 60초 종료, 마이크 거부, 권한 대기 취소, STT 대기 취소, STT 실패, 음성 캐시·URL 해제, 오래된 TTS 응답 무시, 화면 이탈.

실제 배포 전에는 로그인→마이크 권한 허용→녹음→수정→두 분야 검색→출처 확인→답변 듣기를 점검한다. Docker 이미지 빌드와 실제 Whisper·외부 TTS 동작은 로컬 대역 테스트와 별도 검증이 필요하다.
