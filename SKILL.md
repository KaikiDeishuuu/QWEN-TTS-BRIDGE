# SKILL: QWEN-TTS-BRIDGE Operator Knowledge

## 1) System Identity

This is a Qwen-based TTS bridge service: a FastAPI microservice that converts text to audio for internal bot/agent callers.

## 2) Core Components

- **HTTP API**: `tts_bridge/server.py`
  - `GET /health`
  - `POST /tts`
- **Auth**: bearer token validation using `INTERNAL_TTS_TOKEN` (`tts_bridge/auth.py`)
- **Config**: environment-driven settings loader (`tts_bridge/config.py`)
- **Synthesis provider**: DashScope/Qwen realtime websocket client (`tts_bridge/qwen_client.py`)
- **Audio pipeline**: PCM synthesis + format encoding via ffmpeg (`tts_bridge/audio_utils.py`)
- **Delivery policy/fallbacks**: routing + fallback plan logic (`delivery_planner.py`, `channel_routing.py`)

## 3) Execution Model

- Runs via `uvicorn tts_bridge.server:app` from `/opt/venv/bin/uvicorn`.
- Standard runtime path is Docker Compose (`docker-compose.yml`).
- Stateless service; all behavior is environment-configured.
- Container environment is populated from `.env` via Compose `env_file`.

## 4) Critical Rules

- NEVER hardcode API keys or tokens.
- ALWAYS read secrets/config from environment variables.
- ALWAYS keep required vars enforced at startup (`DASHSCOPE_API_KEY`, `INTERNAL_TTS_TOKEN`).
- ALWAYS validate synthesized audio length before returning.
- ALWAYS return a correct response media type (`audio/wav`, `audio/mpeg`, `audio/ogg`, or JSON fallback).
- NEVER remove auth checks from `/tts`.

## 5) Integration Context

- Typical caller: OpenClaw/bot/automation service.
- Caller must include `Authorization: Bearer <INTERNAL_TTS_TOKEN>` for `/tts`.
- Service may degrade to structured JSON fallback when synthesis/delivery fails.
- Feishu-specific flow requires `FEISHU_APP_ID` + `FEISHU_APP_SECRET`.

## 6) Failure Modes

- **Missing env at startup**: process crashes with clear error (required vars).
- **Bad typed env values**: startup fails (int/float parse errors).
- **Provider failure/network issues**: synthesis fails and may fallback.
- **Invalid/too-small PCM**: request returns fallback JSON.
- **Auth failure**: `401` (missing/format) or `403` (token mismatch).

## 7) Extension Guidance

- Add provider integrations in synthesis layer (`_synthesize_with_plan`, provider registry in `delivery_planner`).
- Add text pre/post-processing near request handling in `server.py` before/after synthesis.
- Keep response contract stable:
  - Binary audio on success
  - JSON fallback payload on degraded path
- When adding new env vars:
  - Define in `config.py` with typed parsing
  - Document in `.env.example`, `README.md`, and `DEPLOY.md`
