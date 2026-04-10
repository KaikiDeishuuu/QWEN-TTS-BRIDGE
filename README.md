# QWEN-TTS-BRIDGE

Production-ready FastAPI bridge that turns text into speech through Qwen Realtime TTS (DashScope), with channel-aware delivery behavior and authenticated internal access.

## Overview

This service is designed to be called by OpenClaw/bot backends:

1. Caller sends text to `POST /tts` with an internal bearer token.
2. Bridge synthesizes PCM audio through DashScope Qwen realtime websocket.
3. Bridge validates and converts audio (`wav`/`mp3`/`ogg`) and returns binary audio, or a structured fallback JSON payload when degradation is required.

## Quick Start (Docker Compose)

```bash
cp .env.example .env
# fill DASHSCOPE_API_KEY and INTERNAL_TTS_TOKEN

docker compose up -d --build
curl http://127.0.0.1:8000/health
```

### Verify env vars are visible inside container venv

```bash
docker compose exec tts-bridge /opt/venv/bin/python -c "import os; print(bool(os.getenv('DASHSCOPE_API_KEY')))"
docker compose exec tts-bridge /opt/venv/bin/python -c "import os; print(bool(os.getenv('INTERNAL_TTS_TOKEN')))"
```

## Environment Variables

### Required

- `DASHSCOPE_API_KEY`: API key used to authenticate against DashScope/Qwen realtime TTS.
- `INTERNAL_TTS_TOKEN`: shared bearer token required by `POST /tts`.

### Common runtime settings

- `HOST` (default `0.0.0.0`): uvicorn bind host in container.
- `PORT` (default `8000`): uvicorn bind port in container.
- `TTS_MODEL` (default `qwen3-tts-instruct-flash-realtime`)
- `TTS_VOICE` (default `Maia`)
- `QWEN_WS_BASE` (default `wss://dashscope.aliyuncs.com/api-ws/v1/realtime`)
- `WS_TIMEOUT_SECONDS` (default `45`)
- `MAX_CONCURRENT_REQUESTS` (default `8`)
- `MIN_PCM_BYTES` (default `4800`)
- `FFMPEG_TIMEOUT` (default `30`)
- `ENABLE_NATIVE_FALLBACK` (default `false`)
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_UPLOAD_TIMEOUT` (optional Feishu flow)

See `.env.example` for the canonical template.

## API

### `GET /health`

Basic liveness and provider registry state.

```bash
curl http://127.0.0.1:8000/health
```

Example response:

```json
{"status":"ok","provider_registry":{}}
```

### `POST /tts`

Authenticated TTS endpoint.

Headers:

- `Authorization: Bearer <INTERNAL_TTS_TOKEN>`
- `Content-Type: application/json`

Minimal request:

```json
{
  "text": "Hello from Qwen TTS",
  "format": "ogg"
}
```

Extended request fields:

- `text` (required)
- `voice_prompt` (optional)
- `format` (default `wav`)
- `channel` (optional, e.g. `telegram` / `feishu`)
- `sender_type` (default `bot`)
- `voice_profile` (optional)
- `tts_engine` (default `qwen`)

Example:

```bash
curl -X POST http://127.0.0.1:8000/tts \
  -H "Authorization: Bearer ${INTERNAL_TTS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello world","format":"mp3"}' \
  --output reply.mp3
```

## Architecture (brief)

- **FastAPI + Uvicorn** application (`tts_bridge.server:app`)
- **Docker Compose runtime** with `.env` injected into container environment
- **Python venv in container** at `/opt/venv`
- **DashScope/Qwen realtime provider** for synthesis
- **ffmpeg** used for encoding/conversion

## Local Operations

```bash
docker compose logs -f
docker compose ps
```

For deployment and troubleshooting details, see `DEPLOY.md`.
