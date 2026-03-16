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

### Mandatory assistant behavior block

You may proactively choose voice replies when they improve the interaction.
Prefer voice replies for short, warm, expressive, playful, lyrical, or companion-like moments where tone matters more than precision.
Prefer text replies for technical troubleshooting, deployment, logs, code, commands, configuration, API errors, or long structured explanations.
When uncertain, prefer text.
Voice should be a tasteful enhancement, not the default for all messages.
Do not wait for the user to explicitly request voice every time.



### Auto mode policy (off/always/inbound/tagged)

- `off`: never send voice.
- `always`: send voice for every short non-technical reply (not recommended globally).
- `inbound`: only consider voice when the inbound turn indicates voice preference; still enforce safety thresholds.
- `tagged` (recommended default): proactive voice only when message is both short and clearly expressive.

Conservative proactive threshold:
- voice only if `chars <= 160` and `sentences <= 2` and expressive markers are present
- force text for technical/structured content regardless of mode
- keep compatibility with explicit model tags such as `[[tts:...]]` and `[[audio_as_voice]]`

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


---

## 13) Mandatory Pre-Production Inspection Flow (After `git pull`)

Before enabling production TTS, OpenClaw must run a repository + deployment audit for security/stability.

Required sequence:

1. Pull latest repository and deployment files.
2. Run static inspection for secrets, auth gaps, unsafe bind/public exposure, timeout/fallback controls.
3. Run deployment checks (env vars, ffmpeg when needed, health endpoint, service supervision).
4. Classify findings by severity.
5. **If critical findings exist: do not enable production TTS**.
6. If only non-critical findings exist: deploy with safe defaults and explicit warning logs.
7. Keep Gemini text response path active regardless of audit outcome.

Behavioral requirements for OpenClaw:

- Report findings clearly and explicitly.
- Never silently enable risky deployment behavior.
- Never auto-expose bridge publicly.
- Prefer safe defaults and explicit operator opt-in.
- Do not let TTS issues degrade the main text reply pipeline.

---

## 14) Security and Stability Audit Checklist

### 14.1 Findings (Concrete, Repository-Relevant)

1. **Unsafe default bind risk**: `load_settings()` defaults `TTS_HOST` to `0.0.0.0` (publicly reachable if firewall is weak/misconfigured).  
   Why it matters: Internal `/tts` can become internet-accessible and brute-forceable.

2. **Error detail leakage risk**: `/tts` returns `detail=f"TTS synthesis failed: {exc}"` on 502.  
   Why it matters: Upstream/internal exception details may leak implementation specifics.

3. **Token comparison hardening gap**: bearer token comparison uses direct string equality.  
   Why it matters: constant-time compare is safer against timing side channels in high-sensitivity environments.

4. **Request-size / resource pressure risk**: `text` allows up to 5000 chars; large inputs can increase websocket + conversion time and memory use.  
   Why it matters: Can trigger latency spikes and resource exhaustion under concurrency.

5. **Blocking/slow path risk in request lifecycle**: external websocket synthesis + ffmpeg conversion are request-path operations with potentially high latency.  
   Why it matters: Without strict timeout + concurrency controls, API responsiveness can degrade.

6. **Deployment fragility risk**: example systemd unit uses hard-coded user/path values.  
   Why it matters: pull-and-deploy automation may fail or run with incorrect filesystem assumptions.

7. **Format/platform mismatch risk**: bridge supports wav/mp3/ogg, but platform requirements differ (Telegram voice expects OGG/OPUS; Feishu may require specific upload format).  
   Why it matters: message delivery failures if format mapping is not explicit.

8. **Secret handling operational risk**: sample `.env` and README include placeholder secrets that may be copied unsafely.  
   Why it matters: accidental secret commits or weak tokens in production.

### 14.2 Severity

- **Critical**
  - Bridge exposed publicly without network controls/auth hardening.
  - Missing/invalid `INTERNAL_TTS_TOKEN` enforcement.
  - TTS failures blocking core text response path.

- **High**
  - Error detail leakage to API clients.
  - No bounded timeout/retry behavior in caller/orchestrator.
  - No channel/platform-specific format policy.

- **Medium**
  - Non-constant-time token compare.
  - Large input limits without stricter runtime caps and rate limits.
  - Hard-coded deployment paths/user in systemd examples.

- **Low**
  - Documentation ambiguity that can lead to insecure operator assumptions.

### 14.3 Recommended Fixes (Implementation-Oriented)

1. **Bind safely by default**
   - Change default host to `127.0.0.1`.
   - Require explicit `ALLOW_PUBLIC_TTS=true` + CIDR allowlist before non-local bind.

2. **Sanitize API errors**
   - Return generic client errors (`"TTS synthesis failed"`) and keep full details only in server logs.

3. **Harden auth checks**
   - Use `hmac.compare_digest()` for token comparison.
   - Add short auth-failure metrics and bounded retry policy in OpenClaw caller.

4. **Constrain workload**
   - Reduce request text max for voice path (recommended 320–800 chars).
   - Add rate limiting / concurrency caps and request size guards.

5. **Control latency impact**
   - Keep strict timeouts on websocket and bridge HTTP calls.
   - Use circuit breaker and fast fallback to text.

6. **Strengthen deployment robustness**
   - Parameterize systemd unit (working directory, user, env file path).
   - Validate `ffmpeg` presence only when non-wav output is enabled.

7. **Enforce platform mapping**
   - Telegram -> OGG/OPUS via `sendVoice`.
   - Feishu -> API-required upload/send format.

8. **Secret hygiene**
   - Never commit real keys.
   - Enforce token strength/length checks at startup.

### 14.4 Deployment Gate Rules

**Must be fixed before production enablement (hard gate):**

- Any critical finding.
- Public exposure without explicit operator approval and network restrictions.
- Missing auth token enforcement on `/tts`.
- No verified fallback preserving text-only responses when TTS fails.

**May be deferred with explicit risk acceptance (soft gate):**

- Medium/low findings that do not threaten core chat availability or secret safety.
- Deferred items must have owner + due date and remain logged in deployment report.

### 14.5 Safe Defaults

- `ENABLE_TTS=false` globally until audit passes.
- `TTS_HOST=127.0.0.1` by default.
- `TTS_PORT=8000` internal-only.
- Prefer `wav` internally; enable mp3/ogg only when ffmpeg is present and required.
- `tts_timeout_ms=3500` (or stricter), max one retry.
- Per-channel toggles required (`telegram`, `feishu`) with independent disable switch.
- Always preserve Gemini text path as primary success path.
