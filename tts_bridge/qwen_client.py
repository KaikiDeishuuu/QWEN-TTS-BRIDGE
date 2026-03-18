import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QwenSynthesisConfig:
    ws_base: str
    model: str
    api_key: str
    voice: str = "Maia"
    sample_rate: int = 24000
    mode: str = "commit"


class QwenRealtimeTTSClient:
    def __init__(self, config: QwenSynthesisConfig, timeout_seconds: float = 45.0):
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._ws: ClientConnection | None = None

    async def __aenter__(self) -> "QwenRealtimeTTSClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self) -> None:
        url = f"{self._config.ws_base}?model={self._config.model}"
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        logger.info("Connecting to Qwen realtime websocket", extra={"url": url, "model": self._config.model})
        self._ws = await asyncio.wait_for(
            websockets.connect(url, additional_headers=headers),
            timeout=self._timeout_seconds,
        )

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            logger.info("Qwen websocket closed")
            self._ws = None

    async def synthesize(self, text: str, voice_prompt: str | None = None) -> bytes:
        """Synthesize text to PCM audio, with one automatic retry on connection failure."""
        try:
            return await self._do_synthesize(text, voice_prompt)
        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
            logger.warning(
                "Qwen websocket dropped, reconnecting and retrying once",
                extra={"error": str(e)},
            )
            # Reconnect and retry once
            await self.close()
            await self.connect()
            return await self._do_synthesize(text, voice_prompt)

    async def _do_synthesize(self, text: str, voice_prompt: str | None = None) -> bytes:
        if self._ws is None:
            raise RuntimeError("WebSocket is not connected")

        session = {
            "mode": self._config.mode,
            "voice": self._config.voice,
            "response_format": "pcm",
            "sample_rate": self._config.sample_rate,
        }
        if voice_prompt:
            session["instructions"] = voice_prompt
            session["optimize_instructions"] = True

        await self._send_event({"type": "session.update", "session": session})
        await self._send_event({"type": "input_text_buffer.append", "text": text})
        await self._send_event({"type": "input_text_buffer.commit"})

        chunks: list[bytes] = []
        response_done = False

        async def _recv_message() -> dict[str, Any]:
            raw = await self._ws.recv()
            return json.loads(raw)

        start = time.monotonic()
        while not response_done:
            if (time.monotonic() - start) > self._timeout_seconds:
                raise TimeoutError("Timeout waiting for TTS response")

            event = await asyncio.wait_for(_recv_message(), timeout=self._timeout_seconds)
            event_type = event.get("type")

            if event_type == "response.audio.delta":
                delta = event.get("delta") or ""
                chunks.append(base64.b64decode(delta))
            elif event_type == "response.done":
                response_done = True
            elif event_type == "error":
                detail = event.get("error", {})
                raise RuntimeError(f"Qwen realtime error: {detail}")

        pcm_audio = b"".join(chunks)
        logger.info("Synthesis complete", extra={"pcm_bytes": len(pcm_audio)})
        return pcm_audio

    async def _send_event(self, event: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket is not connected")
        event["event_id"] = f"event_{int(time.time() * 1000)}"
        await self._ws.send(json.dumps(event, ensure_ascii=False))
