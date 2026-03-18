# OpenClaw TTS Bridge: Integration & Runtime Instructions

This document is the authoritative reference for **OpenClaw** operators and integrators. It covers the API contract, decision policy, fallback rules, monitoring, and security requirements.

---

## 1. Architecture Overview

```
User (Telegram / Feishu)
  └── OpenClaw (Gemini reasoning)
        └── POST /tts  →  TTS Bridge (FastAPI)
              └── Qwen3 Realtime TTS (WebSocket)
                    └── PCM → FFmpeg → OGG / MP3 / WAV
                          └── Feishu: upload → file_key → msg_type: audio
                          └── Telegram: binary stream → sendVoice
```

---

## 2. API Reference

### POST `/tts`

**Auth**: `Authorization: Bearer <INTERNAL_TTS_TOKEN>`

**Request body**:
```json
{
  "text": "string (1–5000 chars)",
  "channel": "telegram | feishu | other",
  "voice_profile": "companion | playful | professional | neutral",
  "format": "wav | mp3 | ogg",
  "tts_engine": "qwen"
}
```

**Response variants**:

| Scenario | Content-Type | Body |
|---|---|---|
| Telegram / other (success) | `audio/ogg` or `audio/wav` | Binary audio bytes |
| Feishu (success) | `application/json` | `{"msg_type":"audio","content":{"file_key":"..."}}` |
| Any failure / fallback | `application/json` | `{"fallback_to_text":true,"reason":"..."}` |

**Response header**: `X-Request-Id: <uuid>` — always present; use for log correlation.

**Reason codes** (fallback responses):

| Code | Meaning |
|---|---|
| `timeout` | Qwen WebSocket timeout |
| `synthesis_failed` | Qwen API internal error |
| `invalid_audio` | PCM output empty or too small |
| `encoding_timeout` | FFmpeg exceeded time limit |
| `encoding_failed` | FFmpeg error |
| `feishu_upload_failed` | Feishu API error (after retries) |
| `internal_error` | Unhandled server error |

---

## 3. Voice Personality

Auto-selected by text intent; can be overridden via `voice_profile` field or `[[tts:voice=xxx]]` tag.

| Profile | Trigger | Qwen Voice |
|---|---|---|
| `companion` | ❤️ 😊 love miss you | Chelsie |
| `playful` | hi hello haha yay | Ethan |
| `professional` | error log sudo traceback | Serena |
| `neutral` | everything else | Cherry / Maia |

---

## 4. Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `DASHSCOPE_API_KEY` | — | ✅ | Qwen API key |
| `INTERNAL_TTS_TOKEN` | — | ✅ | Shared Bearer token |
| `FEISHU_APP_ID` | `""` | Feishu only | Feishu app credentials |
| `FEISHU_APP_SECRET` | `""` | Feishu only | Feishu app credentials |
| `TTS_HOST` | `127.0.0.1` | — | Bind interface |
| `TTS_PORT` | `5200` | — | Bind port |
| `TTS_VOICE` | `Maia` | — | Default voice fallback |
| `WS_TIMEOUT_SECONDS` | `45` | — | Qwen WebSocket timeout |
| `MAX_CONCURRENT_REQUESTS` | `8` | — | Semaphore cap |
| `FEISHU_UPLOAD_TIMEOUT` | `20` | — | Feishu upload HTTP timeout |
| `MIN_PCM_BYTES` | `4800` | — | Min valid PCM size (~0.1 s) |
| `FFMPEG_TIMEOUT` | `30` | — | FFmpeg subprocess timeout |

---

## 5. Decision & Fallback Policy

### 5.1 Voice vs Text

- **Prefer voice**: `< 160` chars AND `≤ 2` sentences AND expressive markers (emoji / greetings / emotional words).
- **Force text**: Any fenced code, shell commands (`sudo`, `pip`, `apt`), traces (`traceback`, `ERROR`), JSON/YAML, tables, or `> 400` chars.

### 5.2 Failure Fallback

- **`fallback_to_text: true`**: OpenClaw **must** send the Gemini text reply; do not write audio file.
- **Telegram `VOICE_MESSAGES_FORBIDDEN`**: retry with `sendAudio`, then `sendDocument`.
- **Binary `< 512` bytes**: treat as failure.
- **`encoding_timeout` / `encoding_failed`**: do not retry; go to text.

---

## 6. OpenClaw Integration Contract

> [!IMPORTANT]
> These checks are **mandatory** on the OpenClaw side to prevent 0-byte file issues.

```python
response = await call_tts_bridge(text=reply, channel=channel)

# Guard 1: content-type check
if "application/json" in response.headers.get("content-type", ""):
    payload = response.json()
    if payload.get("fallback_to_text"):
        return await send_text(chat_id, reply_text)
    if payload.get("msg_type") == "audio":
        # Feishu native bubble
        return await feishu_send_audio(chat_id, payload["content"]["file_key"])

# Guard 2: size check for binary responses
if len(response.content) < 512:
    return await send_text(chat_id, reply_text)

# Normal binary audio (Telegram, etc.)
with open(tmp_path, "wb") as f:
    f.write(response.content)
```

---

## 7. Monitoring & Alerting

Every successful `/tts` call emits a structured log with these fields:

```
request_id, channel, effective_format, audio_bytes,
final_msg_type, latency_ms, voice_profile, tts_engine
```

Fallback/error calls emit: `request_id, channel, reason`

**Recommended alert thresholds**:

| Metric | Warning | Critical |
|---|---|---|
| `latency_ms` | > 8 000 ms | > 15 000 ms |
| `fallback_to_text` rate | > 5% | > 20% |
| `synthesis_failed` count | > 3/min | > 10/min |
| `feishu_upload_failed` count | > 2/min | > 5/min |

**Query example** (journald):
```bash
# See all fallbacks in last hour
sudo journalctl -u tts-bridge --since "1 hour ago" | grep fallback_to_text

# Tail latency by request-id
sudo journalctl -u tts-bridge -f | grep -E "latency_ms|request_id"
```

---

## 8. Operational Checklist

- [ ] `GET /health` → `{"status": "ok"}`
- [ ] `ffmpeg -version` succeeds (Opus conversion required)
- [ ] `INTERNAL_TTS_TOKEN` matches between OpenClaw and Bridge
- [ ] `FEISHU_APP_ID` / `FEISHU_APP_SECRET` set for Feishu bubbles
- [ ] `TTS_HOST=127.0.0.1` (internal only)
- [ ] systemd: `Restart=always`, `RestartSec=5`
