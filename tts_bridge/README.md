# OpenClaw TTS Bridge (Gemini + Qwen3 Realtime TTS)

This microservice provides speech synthesis for OpenClaw while keeping Gemini as the LLM.

## Architecture

Telegram / Feishu -> OpenClaw Agent -> Gemini (text reply) -> TTS Bridge (`/tts`) -> Qwen3 Realtime TTS WebSocket -> Audio file -> OpenClaw sends voice message.

- Gemini is used for text generation.
- Qwen is used only for text-to-speech.
- TTS Bridge runs independently as an internal HTTP service.

## Project structure

```text
tts_bridge/
├── __init__.py
├── server.py
├── qwen_client.py
├── audio_utils.py
├── auth.py
├── config.py
├── requirements.txt
└── README.md
```

## Installation

### 1) Create venv

```bash
cd /opt/openclaw
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r tts_bridge/requirements.txt
```

### 2) Install FFmpeg

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

## Environment variables

```bash
export DASHSCOPE_API_KEY="your_dashscope_api_key"
export TTS_MODEL="qwen3-tts-instruct-flash-realtime"
export TTS_HOST="0.0.0.0"
export TTS_PORT="8000"
export INTERNAL_TTS_TOKEN="strong-internal-shared-token"
# optional
export QWEN_WS_BASE="wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
export WS_TIMEOUT_SECONDS="45"
```

## Run locally

```bash
uvicorn tts_bridge.server:app --host 0.0.0.0 --port 8000
```

## API

### `GET /health`

Returns:

```json
{"status":"ok"}
```

### `POST /tts`

Headers:

```text
Authorization: Bearer INTERNAL_TTS_TOKEN
Content-Type: application/json
```

Request body:

```json
{
  "text": "Hello world",
  "voice_prompt": "warm conversational assistant voice, natural tone, slightly slower speech",
  "format": "wav"
}
```

Supported `format`: `wav`, `mp3`, `ogg`.

The service:
1. Validates bearer token.
2. Connects to Qwen realtime websocket.
3. Sends `session.update` + text commit events.
4. Streams PCM chunks and reassembles audio.
5. Converts PCM -> target format with FFmpeg.
6. Returns binary audio bytes.

## Deployment (systemd)

1. Copy service file:

```bash
sudo cp deploy/tts-bridge.service /etc/systemd/system/tts-bridge.service
```

2. Create env file `/opt/openclaw/tts_bridge/.env`:

```bash
DASHSCOPE_API_KEY=your_key
TTS_MODEL=qwen3-tts-instruct-flash-realtime
TTS_HOST=0.0.0.0
TTS_PORT=8000
INTERNAL_TTS_TOKEN=strong-token
```

3. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tts-bridge
sudo systemctl start tts-bridge
sudo systemctl status tts-bridge
```

Logs are available in journald:

```bash
journalctl -u tts-bridge -f
```

## OpenClaw integration

Enable a feature flag and switch to voice-first response.

```python
# openclaw_voice_pipeline.py (example)
import os
import httpx

ENABLE_TTS = os.getenv("ENABLE_TTS", "false").lower() == "true"
TTS_BRIDGE_URL = os.getenv("TTS_BRIDGE_URL", "http://127.0.0.1:8000/tts")
INTERNAL_TTS_TOKEN = os.getenv("INTERNAL_TTS_TOKEN", "")


async def respond_with_voice_or_text(platform: str, chat_id: str, llm_text: str):
    if not ENABLE_TTS:
        return await send_text(platform, chat_id, llm_text)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                TTS_BRIDGE_URL,
                headers={"Authorization": f"Bearer {INTERNAL_TTS_TOKEN}"},
                json={
                    "text": llm_text,
                    "voice_prompt": "warm conversational assistant voice",
                    "format": "ogg" if platform == "telegram" else "mp3",
                },
            )
            r.raise_for_status()

        audio_bytes = r.content
        return await send_voice(platform, chat_id, audio_bytes)
    except Exception:
        # fallback to plain text when TTS fails
        return await send_text(platform, chat_id, llm_text)
```

Required OpenClaw env:

```bash
ENABLE_TTS=true
TTS_BRIDGE_URL=http://127.0.0.1:8000/tts
INTERNAL_TTS_TOKEN=strong-token
```

## Telegram voice integration (OGG/OPUS)

Telegram `sendVoice` expects OGG/OPUS. Request `format=ogg` from TTS bridge:

```python
import httpx

async def send_telegram_voice(bot_token: str, chat_id: str, text: str, bridge_url: str, internal_token: str):
    async with httpx.AsyncClient(timeout=45) as client:
        tts = await client.post(
            bridge_url,
            headers={"Authorization": f"Bearer {internal_token}"},
            json={"text": text, "format": "ogg"},
        )
        tts.raise_for_status()

        files = {"voice": ("reply.ogg", tts.content, "audio/ogg")}
        data = {"chat_id": chat_id}
        tg = await client.post(f"https://api.telegram.org/bot{bot_token}/sendVoice", data=data, files=files)
        tg.raise_for_status()
        return tg.json()
```

## Feishu voice integration

Typical flow: upload file -> send audio message.

```python
import httpx


async def send_feishu_audio(tenant_access_token: str, receive_id: str, audio_bytes: bytes, duration_ms: int = 3000):
    headers = {"Authorization": f"Bearer {tenant_access_token}"}

    async with httpx.AsyncClient(timeout=45) as client:
        files = {
            "file_type": (None, "opus"),
            "file_name": (None, "reply.ogg"),
            "file": ("reply.ogg", audio_bytes, "audio/ogg"),
        }
        upload = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers=headers,
            files=files,
        )
        upload.raise_for_status()
        file_key = upload.json()["data"]["file_key"]

        payload = {
            "receive_id": receive_id,
            "msg_type": "audio",
            "content": f'{{"file_key":"{file_key}","duration":{duration_ms}}}',
        }
        send = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        send.raise_for_status()
        return send.json()
```

## Test command

```bash
curl -X POST http://localhost:8000/tts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer INTERNAL_TTS_TOKEN" \
  -d '{"text":"Hello this is a voice test","format":"wav"}' \
  --output test.wav
```


## Channel-aware proactive TTS routing (reference implementation)

Use `tts_bridge/channel_routing.py` as the policy layer between LLM text and channel-specific sending.

Highlights:
- Supports auto modes: `off`, `always`, `inbound`, `tagged` (recommended default).
- Conservative proactive voice gate: short + clearly expressive only.
- Technical and long responses are forced to text.
- Preserves compatibility with `[[tts:...]]` and `[[audio_as_voice]]` tags.
- Telegram path: voice-note payload (`sendVoice` style with `asVoice=true` / `ptt=true`).
- Feishu path: convert to opus -> upload -> send `msg_type="audio"`.
- Bridge response validation rejects JSON error bodies and tiny/non-audio outputs.

Example policy usage:

```python
from tts_bridge.channel_routing import (
    VoiceDecisionConfig,
    should_use_voice,
    choose_bridge_format,
    validate_bridge_response,
    build_send_plan,
)

cfg = VoiceDecisionConfig(mode="tagged", max_chars=160, max_sentences=2, min_expressive_hits=1)
use_voice, reason = should_use_voice(reply_text, channel, cfg, inbound_voice_hint=inbound_voice_hint)
fmt = choose_bridge_format(channel)

# structured debug logs (recommended)
logger.info("tts_route", extra={"channel": channel, "decision": use_voice, "reason": reason, "format": fmt})
```

### Feishu voice bubble checklist

1. Generate bridge audio.
2. Validate `content-type` and output size.
3. Convert to opus when source is mp3/wav.
4. Upload to Feishu files API.
5. Require returned `file_key`.
6. Send message with `msg_type="audio"`.
7. If any step fails, send text fallback and log failure reason.

### Telegram voice bubble checklist

1. Select `format=ogg` for Telegram.
2. Send using Telegram voice-note endpoint (not generic file endpoint).
3. Ensure equivalent voice-note flags: `asVoice=true` / `[[audio_as_voice]]` / `ptt=true`.
4. Log channel, provider path, voice-note flag state, mime, and output bytes.


## Voice Personality System

The repository now includes `tts_bridge/voice_personality.py` for session-consistent voice behavior:

- Profiles: `companion`, `playful`, `professional`, `neutral`
- Engine compatibility: Edge + Qwen voice IDs
- Session lock behavior: first expressive selection locks profile per `session_id`
- Manual override: `[[tts:voice=companion]]`, `[[tts:voice=playful]]`, etc.

Production logging fields to include during routing/sending:
- `channel`
- `voice_profile`
- `tts_engine`
- `requested_format`
- `effective_format`
- `audio_bytes`
- `voice_flags`

Feishu bubble delivery contract:
- Never send generic file attachment for voice
- Convert to opus and upload, then send `msg_type="audio"`

