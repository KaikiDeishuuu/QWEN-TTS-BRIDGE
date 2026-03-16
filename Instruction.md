# OpenClaw Runtime Instruction: Gemini + Internal Qwen TTS Bridge

## 1) Scope and Role Definition

OpenClaw is the **primary orchestrator**.

- **Main LLM**: Gemini (must remain unchanged).
- **Speech synthesis only**: Qwen3 Realtime TTS via internal TTS Bridge HTTP service.
- **TTS Bridge role**: backend capability service only; never used for reasoning or response planning.

OpenClaw responsibilities:

1. Pull repository updates.
2. Read deployment/runtime configuration.
3. Start/restart TTS Bridge when configuration is valid.
4. Generate text replies with Gemini.
5. Decide if a text reply should also be spoken.
6. Call TTS Bridge only when TTS policy allows.
7. Send voice to Telegram/Feishu when supported and enabled.
8. Fall back to text when TTS is unavailable or fails.

---

## 2) Deployment Rules (Linux Server, No GPU, No Local Audio)

After `git pull`, OpenClaw should run this deployment workflow:

1. Detect bridge directory: `./tts_bridge`.
2. Create/reuse Python virtual environment (for example `.venv`).
3. Install dependencies from `tts_bridge/requirements.txt`.
4. Validate required environment variables:
   - `DASHSCOPE_API_KEY`
   - `INTERNAL_TTS_TOKEN`
5. Validate optional conversion dependency:
   - if mp3/ogg conversion enabled, ensure `ffmpeg` exists.
6. Run bridge health check: `GET /health` must return `{"status":"ok"}`.
7. On Linux server, deploy/manage with systemd.
8. Restart bridge on code or config change.
9. Refuse public exposure unless explicitly configured.

### Required deployment behavior on failure

If any check fails, OpenClaw must:

- Log exact failure reason (structured logs).
- Keep chat text pipeline fully available.
- Mark TTS as degraded/disabled, not fatal.

### systemd rules

- Bind to `127.0.0.1` or private/internal network by default.
- `Restart=always` with sane backoff.
- Separate service logs for bridge troubleshooting.
- Never require GPU or playback devices.

---

## 3) Runtime TTS Decision Policy

TTS is an enhancement layer, not a dependency.

### Use TTS for

- Greetings and short daily conversation.
- Lightweight assistant confirmations.
- Natural, concise, friendly interactive replies.
- Emotionally expressive but short responses.

### Do NOT use TTS for

- Long explanations or multi-paragraph analysis.
- Technical walkthroughs/debugging/code answers.
- Legal/medical/financial caution-heavy content.
- Markdown-heavy or reference-formatted outputs.
- Tables, code blocks, logs, stack traces.
- Responses containing many URLs/commands/config fragments.
- Long academic or highly structured instruction text.

### Hard policy rules

- If reply is long, dense, technical, or explanatory -> **text only**.
- If reply is short and conversational -> voice is allowed.
- If both are useful -> send text first; optional short voice summary.

### Recommended threshold defaults (configurable)

- `tts_max_chars = 320` (or stricter by platform).
- `tts_max_sentences = 4`.
- Force text-only if content contains:
  - fenced code (```)
  - shell patterns (`$ `, `sudo`, `pip`, `apt`, `kubectl`, etc.)
  - log-like lines (`ERROR`, timestamps, stack traces)
  - JSON/YAML markers (`{}`, `[]`, `:`, indentation blocks)
  - markdown tables (`|---`)

---

## 4) Platform Adaptation Rules (Mandatory)

OpenClaw must map TTS output to channel requirements.

### Telegram

- Prefer `ogg/opus` for voice.
- Use Telegram `sendVoice` flow when possible.
- If voice API not available, fallback to text (optional: send as audio file).

### Feishu

- Use format required by Feishu upload/send API.
- Follow Feishu media upload then message send flow.
- If unsupported/failed, fallback to text.

### Cross-platform rule

Platform-specific formatting is mandatory; do not assume one audio format works everywhere.

---

## 5) Failure and Fallback Rules

If TTS generation fails (timeout, auth, upstream, conversion, network):

- Do not fail whole assistant response.
- Log error details with request id/channel/user context.
- Send normal text reply.

If format conversion fails:

1. Try WAV if platform supports it.
2. Otherwise send text only.

If bridge health check fails:

- Temporarily disable TTS.
- Continue normal text chat.
- Retry health checks with bounded backoff.

If token auth fails:

- Do not retry indefinitely.
- Flag internal configuration issue.
- Keep text pipeline unaffected.

---

## 6) Operational Rules

- `ENABLE_TTS=false` by default.
- Support per-channel toggles (`telegram`, `feishu`, etc.).
- Support per-platform format mapping.
- Enforce strict timeout budget for TTS calls.
- No infinite retries.
- Structured logs (`event`, `channel`, `latency_ms`, `status`, `error_code`).
- Never block core chat flow waiting on slow TTS.

### Recommended timeout/retry defaults

- Bridge request timeout: `2.5s - 5s`.
- Retries: at most `1` quick retry for transient network errors.
- Circuit breaker: open after repeated failures; auto half-open probes.

---

## 7) Security Rules

- Require internal Bearer token for bridge calls.
- No public unauthenticated `/tts` endpoint.
- Secrets via environment variables only.
- No hard-coded keys/tokens in repo.
- Default bind to localhost or private network.
- Explicit operator warning/flag required before internet exposure.

---

## 8) Example OpenClaw Configuration Structure

```yaml
llm:
  provider: gemini
  model: gemini-2.5-flash

tts:
  enabled: false               # global default
  timeout_ms: 3500
  max_chars: 320
  max_sentences: 4
  internal_bridge:
    base_url: http://127.0.0.1:8000
    bearer_token_env: INTERNAL_TTS_TOKEN
    health_path: /health
    synth_path: /tts
  channels:
    telegram:
      enabled: true
      preferred_format: ogg_opus
      send_mode: voice
    feishu:
      enabled: true
      preferred_format: feishu_required
      send_mode: audio_upload
  disable_patterns:
    - "```"
    - "|---"
    - "sudo "
    - "kubectl "
    - "Traceback (most recent call last):"
```

---

## 9) Example Runtime Decision Logic (Pseudo-code)

```python
def should_use_tts(reply_text: str, channel: str, cfg) -> bool:
    if not cfg.tts.enabled:
        return False
    if not cfg.tts.channels[channel].enabled:
        return False

    if len(reply_text) > cfg.tts.max_chars:
        return False
    if sentence_count(reply_text) > cfg.tts.max_sentences:
        return False

    if contains_code_or_commands(reply_text):
        return False
    if contains_logs_or_structured_config(reply_text):
        return False
    if is_reference_style_explanation(reply_text):
        return False

    return is_conversational(reply_text)
```

```python
def choose_audio_format(channel: str, cfg) -> str:
    if channel == "telegram":
        return "ogg_opus"
    if channel == "feishu":
        return "feishu_required"
    return "wav"
```

```python
def synthesize_with_fallback(reply_text, channel, cfg):
    # Always generate/send text via Gemini first or in parallel-safe flow.
    text_payload = build_text_response(reply_text)

    if not should_use_tts(reply_text, channel, cfg):
        return send_text(channel, text_payload)

    audio_format = choose_audio_format(channel, cfg)

    try:
        audio = call_tts_bridge(
            text=reply_text,
            fmt=audio_format,
            timeout_ms=cfg.tts.timeout_ms,
            bearer_token=os.getenv("INTERNAL_TTS_TOKEN"),
        )
        return send_voice_or_audio(channel, audio, audio_format, text_fallback=text_payload)
    except AuthError:
        log_event("tts_auth_failed", channel=channel)
        return send_text(channel, text_payload)
    except FormatConversionError:
        try:
            wav = call_tts_bridge(reply_text, fmt="wav", timeout_ms=cfg.tts.timeout_ms)
            return send_voice_or_audio(channel, wav, "wav", text_fallback=text_payload)
        except Exception:
            log_event("tts_conversion_failed", channel=channel)
            return send_text(channel, text_payload)
    except Exception as e:
        log_event("tts_failed", channel=channel, error=str(e))
        return send_text(channel, text_payload)
```

---

## 10) Concise Deployment Workflow for OpenClaw Agents

1. `git pull` repository.
2. Detect `tts_bridge` path.
3. Ensure `.venv` exists and activate.
4. `pip install -r tts_bridge/requirements.txt`.
5. Validate env vars and token config.
6. Verify `ffmpeg` if conversion enabled.
7. Start/restart systemd unit (`tts-bridge.service`).
8. Poll `/health` until healthy or timeout.
9. If unhealthy, set `tts.enabled=false` in runtime state and continue text chat.

---

## 11) Recommended ENABLE_TTS Policy

- Global default: disabled (`ENABLE_TTS=false`).
- Enable only after:
  - bridge health check success,
  - auth token present,
  - platform integration validated.
- Allow per-channel opt-in and runtime hot-disable during incidents.

---

## 12) Non-Negotiable Guardrails

- Gemini remains the only reasoning/chat LLM.
- Qwen path is speech synthesis only.
- TTS failures never degrade or block main response pipeline.
- Long technical explanations remain text-only.
- Voice usage is selective, short, conversational, and platform-compliant.
