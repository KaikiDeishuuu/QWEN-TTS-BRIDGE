# Deterministic Message Runtime Kernel Instructions

This file is the authoritative runtime contract for OpenClaw integrations that use this bridge. These rules are mandatory. Code, tests, and operators MUST treat violations as defects.

## 1. System Invariants

1. Every request MUST have a unique `request_id` before planning starts.
2. Every delivery decision MUST be produced exactly once by `decide_audio_delivery()`.
3. No downstream component may change `channel`, `sender_type`, `tts_provider`, `resolved_type`, `fallback_chain`, or `reason_codes` after the `DeliveryPlan` is created.
4. Audio delivery MUST be rejected before synthesis when channel capability or sender capability is unsupported.
5. Provider selection MUST be deterministic and based only on registry state, channel capabilities, and request attributes.
6. Fallback execution MUST be ordered, finite, and terminate in `text` or a terminal error.
7. Every non-success path MUST emit a structured reason code.
8. Every externally visible response MUST return `X-Request-Id`.
9. Feishu audio MUST be BOT-only and MUST use uploaded Opus voice delivery.
10. Telegram transport MUST be selected from the deterministic table in Section 5.4.
11. Silent fallback is forbidden. If the runtime downgrades behavior, it MUST say why.
12. Hidden defaults are forbidden. If a field is omitted, the runtime MUST normalize it explicitly and log the normalized value.

## 2. Runtime Pipeline

The runtime MUST execute the following pipeline in order:

1. **Normalize Request**
   - Normalize `channel` to lowercase.
   - Normalize `sender_type` to lowercase.
   - Assign `request_id`.
   - Reject malformed input before planning.
2. **Capability Gate**
   - Validate channel capability and sender permission.
   - Reject unsupported audio before any provider call.
3. **Provider Selection**
   - Query the provider registry.
   - Select one provider or terminal `none`.
   - Record provider-selection reason codes.
4. **DeliveryPlan Construction**
   - Produce one immutable `DeliveryPlan`.
   - Freeze fallback order and constraints.
5. **Synthesis Execution**
   - Execute the selected provider.
   - Do not switch providers mid-attempt.
6. **Transport Resolution**
   - Resolve transport strictly from `resolved_type`, channel contract, and measured output attributes.
7. **Ordered Fallback Execution**
   - On failure, advance to the next entry in `fallback_chain` exactly once.
   - Stop when a terminal success or terminal text fallback is produced.
8. **Response Emission**
   - Return audio payload, Feishu upload envelope, or text fallback JSON.
   - Include `request_id`, reason metadata, and transport metadata.
9. **Observability Commit**
   - Log plan, attempt, result, reason code, latency, and final outcome.

## 3. Channel Capability Contracts

### 3.1 Feishu
- Audio sender MUST be `bot`.
- `user` audio is forbidden.
- Allowed resolved types: `voice_bubble`, `text`.
- Audio payload MUST be Opus-compatible and uploaded before message emission.
- Direct audio file delivery is forbidden.

### 3.2 Telegram
- Audio sender MAY be `bot` or `user`.
- Allowed resolved types: `voice_bubble`, `audio_file`, `document`, `text`.
- Voice bubble SHOULD be used only for short speech content.
- Long or music-like content MUST use `audio_file`.
- Non-audio payload fallback MUST use `document` before `text` when audio transport is impossible.

### 3.3 Other Channels
- Voice bubble delivery is forbidden.
- Audio delivery MUST use `audio_file` when supported.
- If file audio is unsupported, runtime MUST terminate in `text`.

## 4. Provider Selection Policy

1. External bridge is primary.
2. Native TTS is optional and MUST be enabled explicitly.
3. Provider registry MUST track:
   - registration state
   - health state
   - circuit state
   - last failure time
   - failure count
4. Provider selection order MUST be:
   - healthy bridge
   - half-open bridge probe
   - native fallback when allowed
   - `none`
5. Registry decisions MUST be serialized to avoid concurrent state corruption.
6. The runtime MUST record why bridge was not used with one or more reason codes.
7. Rapid provider flapping is forbidden. Hysteresis MUST keep the current non-terminal provider until the configured recovery threshold is met.

## 5. DeliveryPlan Rules

### 5.1 Immutability
- `DeliveryPlan` MUST be deeply immutable.
- `fallback_chain` MUST be a tuple.
- `reason_codes` MUST be a tuple.
- `constraints` MUST be read-only.
- Any attempt to mutate a plan after construction MUST fail.

### 5.2 Required Fields
A valid `DeliveryPlan` MUST contain:
- `request_id`
- `channel`
- `sender_type`
- `requested_type`
- `resolved_type`
- `tts_provider`
- `bridge_url`
- `fallback_chain`
- `constraints`
- `audio_format`
- `require_opus`
- `reason_codes`
- `status`

### 5.3 Fallback Rules
- `fallback_chain` MUST be complete and ordered before execution starts.
- The chain MUST never be built dynamically after the first failure.
- Terminal chains:
  - Feishu: `voice_bubble -> text`
  - Telegram speech: `voice_bubble -> audio_file -> document -> text`
  - Generic file channels: `audio_file -> text`
- Skipping an intermediate fallback target is forbidden unless the target is contractually unsupported and that fact is logged.

### 5.4 Deterministic Transport Decision Table

| Channel | Resolved Type | Condition | Transport | Rule |
| --- | --- | --- | --- | --- |
| Feishu | `voice_bubble` | always | `im/v1/files` + `im/v1/messages` | upload Opus, then send file key |
| Feishu | `text` | fallback only | text upstream | never send raw audio file |
| Telegram | `voice_bubble` | duration `<= 300s` and MIME starts with `audio/` | `sendVoice` | mark as voice/PTT |
| Telegram | `audio_file` | long-form audio or music | `sendAudio` | preserve audio semantics |
| Telegram | `document` | payload is not valid audio | `sendDocument` | do not fake voice |
| Telegram | `text` | terminal fallback | `sendMessage` | reason code required |
| Other | `audio_file` | audio supported | file transport | no voice bubble emulation |
| Other | `text` | no audio support or terminal failure | text transport | reason code required |

## 6. Fallback Execution Engine

1. Each step MUST include:
   - `attempt_index`
   - `failed_stage`
   - `failed_type`
   - `next_type`
   - `reason_code`
2. The engine MUST advance strictly left-to-right through `fallback_chain`.
3. Repeating the same failed type is forbidden.
4. If the chain is exhausted, runtime MUST emit terminal text fallback or a terminal structured error.
5. Provider failure and transport failure MUST be distinguishable in logs and responses.

## 7. Structured Error Propagation

1. Broad exception handling may only convert failures into structured terminal responses.
2. Every failure response MUST include:
   - `request_id`
   - `reason_code`
   - `resolved_type`
   - `fallback_chain`
   - `plan_reason_codes`
3. Error classes SHOULD map to stable codes, including:
   - `capability_blocked`
   - `bridge_not_registered`
   - `bridge_circuit_open`
   - `bridge_half_open_probe`
   - `bridge_timeout`
   - `bridge_protocol_error`
   - `native_unavailable`
   - `invalid_audio`
   - `ffmpeg_failed`
   - `feishu_credentials_missing`
   - `feishu_upload_failed`
   - `telegram_transport_invalid`
   - `delivery_failed`
4. Returning text fallback without a reason code is forbidden.

## 8. Reason Code System

The runtime MUST maintain reason codes in ordered categories:

- **policy**: capability and sender decisions
- **provider**: registry and provider health decisions
- **transport**: channel-specific transport decisions
- **fallback**: downgrade decisions
- **quality**: audio validity or downgrade decisions

Minimum explainability requirements:
- Why bridge was not used → provider reason code
- Why BOT was selected → policy reason code
- Why fallback triggered → fallback reason code with failed stage
- Why audio was downgraded → transport or quality reason code

## 9. Self-Healing Rules

1. **Provider hysteresis**
   - Bridge recovery MUST require a half-open probe and a configured number of consecutive successes before normal routing resumes.
2. **Retry and backoff**
   - Provider retries MUST be bounded.
   - Backoff MUST be deterministic.
   - Unbounded retry loops are forbidden.
3. **Failure memory**
   - Recent failures MUST affect provider eligibility until expiry.
4. **Shadow mode**
   - Optional dual-run validation MAY execute a non-authoritative secondary provider.
   - Shadow results MUST never alter the authoritative response.
5. **Replay**
   - Runtime MUST allow replay by `request_id` using captured normalized input and recorded plan.
   - Replay MUST preserve original policy decisions unless explicitly run in diagnostic override mode.

## 10. Logging Schema

Every request MUST emit structured events containing, at minimum:
- `request_id`
- `step`
- `channel`
- `sender_type`
- `requested_type`
- `resolved_type`
- `tts_provider`
- `transport_api`
- `attempt_index`
- `latency_ms`
- `reason_codes`
- `terminal_status`

Log events MUST include:
- `request_received`
- `policy_resolved`
- `provider_selected`
- `synthesis_started`
- `synthesis_result`
- `transport_selected`
- `fallback_triggered` when applicable
- `delivery_result`

## 11. Prohibited Behaviors

The following are forbidden:
- changing provider or channel rules outside the planner
- synthesizing audio before capability validation
- mutating `DeliveryPlan`
- implicit BOT/USER substitution
- sending Feishu audio as USER
- silent provider downgrade
- silent text fallback
- random fallback ordering
- unlogged default values
- swallowing provider or transport exceptions without structured propagation

## 12. Testing Requirements

The codebase MUST enforce these rules with automated tests:
1. DeliveryPlan immutability tests
2. Channel capability contract tests per channel and sender type
3. Provider selection invariant tests, including circuit-open and half-open behavior
4. Sender enforcement tests for Feishu BOT-only delivery
5. Transport decision table tests for Telegram, Feishu, and generic channels
6. Fallback chain completeness and termination tests
7. Reason-code propagation tests on all terminal fallback paths
8. Replay determinism tests for the same normalized request

## 13. Text Diagram of the Current Runtime

```text
Client Request
  -> FastAPI /tts endpoint
  -> auth_dependency + bearer token validation
  -> request normalization (channel, sender_type, request_id)
  -> decide_audio_delivery()
       -> channel_caps capability gate
       -> provider_registry.select_provider()
       -> immutable DeliveryPlan construction
  -> resolve_session_voice()
  -> _synthesize_with_plan()
       -> QwenRealtimeTTSClient websocket synthesis
       -> provider success/failure reporting
  -> pcm_to_encoded()
  -> transport resolution
       -> Feishu: convert_to_feishu_opus() -> FeishuClient.upload_audio()
       -> Telegram/Other: build_send_plan() -> binary audio response
  -> ordered fallback response when any stage terminates in text
  -> structured logs for every stage
```
