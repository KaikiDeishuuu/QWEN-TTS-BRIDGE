# OpenClaw TTS Bridge: Integration & Runtime Instructions

This document provides the technical specification and operational policies for the **Qwen TTS Bridge**, enabling OpenClaw to deliver high-quality, channel-aware voice responses.

---

## 1. Architecture Overview

- **Orchestrator**: OpenClaw (Gemini for reasoning).
- **Service**: TTS Bridge (FastAPI).
- **Upstream**: Qwen3 Realtime TTS (WebSocket).
- **Channel Delivery**: 
  - **Telegram**: Bridge returns binary OGG/Opus; OpenClaw sends `sendVoice`.
  - **Feishu**: Bridge converts to Opus, uploads to Feishu API, and returns a `file_key` JSON; OpenClaw sends `msg_type: "audio"`.

---

## 2. API Specification

### POST `/tts`
- **Auth**: `Authorization: Bearer <INTERNAL_TTS_TOKEN>`
- **Request Body**:
```json
{
  "text": "String (1-5000 chars)",
  "channel": "telegram | feishu | other",
  "voice_profile": "companion | playful | professional | neutral", // Optional override
  "format": "wav | mp3 | ogg", // Optional, bridge will auto-override based on channel
  "tts_engine": "qwen"
}
```

### Responses
1. **Binary (Telegram/Standard)**:
   - Returns binary audio stream.
   - Headers: `Content-Type: audio/ogg` (Telegram) or `audio/wav`.

2. **Feishu JSON (Success)**:
```json
{
  "msg_type": "audio",
  "content": {
    "file_key": "vabc_123..."
  }
}
```

3. **Fallback JSON (Error/Threshold)**:
```json
{
  "fallback_to_text": true,
  "reason": "invalid_audio | feishu_upload_failed | synthesis_failed"
}
```

---

## 3. Intelligence Features

### 3.1 Voice Personality
The bridge automatically detects **intent** from text to select the best voice profile:
- **Companion**: Triggered by emojis (❤️, 😊), "love", "miss you". Uses **Chelsie** (warm).
- **Playful**: Triggered by greetings, "haha", "yay". Uses **Ethan** (cheerful).
- **Professional**: Triggered by technical keywords (error, sudo, log). Uses **Serena** (calm).
- **Neutral**: Default for standard information. Uses **Cherry**.

### 3.2 Channel Routing
- **Telegram Override**: Forces `ogg` format for waveform support.
- **Feishu Override**: Forces `ogg` at **32kbps** and triggers the cloud upload pipeline.

---

## 4. Operational Requirements

### Environment Variables (.env)
- `DASHSCOPE_API_KEY`: Required for Qwen API.
- `INTERNAL_TTS_TOKEN`: Shared secret with OpenClaw.
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`: Required for Feishu voice bubbles.
- `TTS_HOST`: Set to `127.0.0.1` for local-only security.

### Dependencies
- **System**: `ffmpeg` must be installed for Opus conversion (Feishu/TG bubbles).
- **Python**: `pip install httpx fastapi uvicorn pydantic-settings websockets`.

### Service Management
- Managed via systemd: `systemctl restart tts-bridge`.
- Logs: `journalctl -u tts-bridge -f`.

---

## 5. Decision & Fallback Policy

### 5.1 Voice vs Text Decision
OpenClaw should actively use voice for **Short & Expressive** messages but **Force Text** for information-dense or technical content.

- **Prefer Voice**: Message is `< 160` characters AND `< 2` sentences AND contains expressive intent/emoji.
- **Force Text-Only**: If message contains any of the following:
  - Fenced code blocks (```)
  - Shell/Terminal commands (`sudo`, `pip`, `apt`, `kubectl`, etc.)
  - Logs or traces (`traceback`, `ERROR`, timestamps)
  - Structured data (JSON, YAML, Markdown Tables)
  - Long academic or highly explanatory blocks (> 400 chars)

### 5.2 Failure & Fallback
- **Safety First**: If the Bridge returns a JSON with `fallback_to_text: true`, OpenClaw **must** deliver the Gemini text response.
- **Binary Check**: If a standard response is binary but `< 1KB`, treat it as a failure and fallback to text.
- **Timeout**: Enforce a strict timeout of `3.5s` for TTS calls to avoid blocking the user experience.

---

## 6. Operational Checklist
- [ ] `GET /health` returns `{"status": "ok"}`.
- [ ] `ffmpeg -version` is successful (required for Opus conversion).
- [ ] `INTERNAL_TTS_TOKEN` is synced between OpenClaw and Bridge.
- [ ] `FEISHU_APP_ID/SECRET` configured for native bubbles.
.
