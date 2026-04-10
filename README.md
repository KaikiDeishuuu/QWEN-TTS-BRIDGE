# QWEN-TTS-BRIDGE

Production-ready FastAPI bridge that turns text into speech through Qwen Realtime TTS (DashScope), with channel-aware delivery behavior and authenticated internal access.

## Overview

This service is designed to be called by OpenClaw/bot backends:

1. Caller sends text to `POST /tts` with an internal bearer token.
2. Bridge synthesizes PCM audio through DashScope Qwen realtime websocket.
3. Bridge validates and converts audio (`wav`/`mp3`/`ogg`) and returns binary audio, or a structured fallback JSON payload when degradation is required.

## Deployment Modes (Important)

This repository currently supports **two deployment modes**:

1. **Systemd + local virtualenv (`venv_bridge`)**
   - This is the **current production mode on Haowei's host**.
   - Bind: `127.0.0.1:5200`
   - Managed by: `systemctl status tts-bridge`
   - OpenClaw on this host currently talks to the bridge through this loopback endpoint.

2. **Docker / docker-compose**
   - Added for portable deployment and future migration scenarios.
   - Default examples in this repo expose port `8000` unless overridden.

> Important: the Docker path is **not** the active production path on this machine right now.
> Do not switch deployment modes casually without also checking OpenClaw integration, env loading, and port alignment.

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

## Current Production Note

On the current host, the live bridge is still started by **systemd** with local virtualenv and loopback bind:

```ini
WorkingDirectory=/root/.openclaw/workspace/Graces_Tools/QWEN-TTS-BRIDGE
EnvironmentFile=/root/.openclaw/workspace/Graces_Tools/QWEN-TTS-BRIDGE/.env
ExecStart=/root/.openclaw/workspace/Graces_Tools/QWEN-TTS-BRIDGE/venv_bridge/bin/uvicorn tts_bridge.server:app --host 127.0.0.1 --port 5200
```

If you follow Docker instructions later, make sure to explicitly reconcile:
- service port (`5200` vs repo Docker default `8000`)
- environment loading
- OpenClaw caller configuration
- health-check endpoint and restart policy

## Local Operations

### Docker path

```bash
docker compose logs -f
docker compose ps
```

### Current host production path

```bash
systemctl status tts-bridge
journalctl -u tts-bridge -n 100 --no-pager
curl http://127.0.0.1:5200/health
```

For deployment and troubleshooting details, see `DEPLOY.md`.
