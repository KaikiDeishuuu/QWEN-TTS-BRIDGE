import asyncio
import logging
import time
import subprocess

import httpx

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/files"

_MAX_UPLOAD_RETRIES = 2       # total attempts = 1 + retries
_RETRY_BACKOFF_S = 1.0        # seconds between retries


class FeishuClient:
    """Handles Feishu tenant token management and audio file uploads."""

    def __init__(self, app_id: str, app_secret: str, upload_timeout: float = 20.0):
        self.app_id = app_id
        self.app_secret = app_secret
        self.upload_timeout = upload_timeout
        self._tenant_access_token: str | None = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> None:
        """Refresh tenant_access_token if absent or within 60 s of expiry."""
        if self._tenant_access_token and time.monotonic() < self._token_expiry:
            return

        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_TOKEN_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Feishu token error: {data.get('msg')}")

        self._tenant_access_token = data["tenant_access_token"]
        # Official TTL is usually 7200 s; refresh 60 s before expiry
        self._token_expiry = time.monotonic() + data.get("expire", 7200) - 60
        logger.info("Feishu tenant_access_token refreshed")

    # ------------------------------------------------------------------
    # Audio duration probe
    # ------------------------------------------------------------------

    def _probe_duration_ms(self, file_path: str) -> int:
        """Best-effort duration probe via ffprobe; returns 3000 ms on failure."""
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    file_path,
                ],
                text=True,
                timeout=5,
            ).strip()
            sec = float(out)
            return max(1, int(sec * 1000))
        except Exception:
            return 3000

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_audio(self, file_path: str) -> str:
        """Upload an Opus file to Feishu and return the file_key.

        Retries up to *_MAX_UPLOAD_RETRIES* times on transient 5xx errors.

        Raises:
            RuntimeError: on non-retryable failure or exhausted retries.
        """
        await self._ensure_token()

        duration_ms = self._probe_duration_ms(file_path)
        headers = {"Authorization": f"Bearer {self._tenant_access_token}"}

        last_error: Exception | None = None
        for attempt in range(1, _MAX_UPLOAD_RETRIES + 2):  # 1-indexed
            try:
                # Open file fresh on each attempt (avoids exhausted file pointer)
                with open(file_path, "rb") as audio_file:
                    files = {"file": ("voice.opus", audio_file, "audio/ogg")}
                    data = {
                        "file_name": "voice.opus",
                        "file_type": "opus",
                        "duration": str(duration_ms),
                    }
                    async with httpx.AsyncClient(timeout=self.upload_timeout) as client:
                        resp = await client.post(
                            _UPLOAD_URL,
                            headers=headers,
                            data=data,
                            files=files,
                        )

                # Retry on server-side errors
                if resp.status_code >= 500:
                    raise RuntimeError(
                        f"Feishu upload returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )

                resp.raise_for_status()
                res = resp.json()

                if res.get("code") != 0:
                    raise RuntimeError(f"Feishu upload API error: {res.get('msg')}")

                file_key: str = res["data"]["file_key"]
                logger.info(
                    "Feishu opus uploaded",
                    extra={
                        "attempt": attempt,
                        "duration_ms": duration_ms,
                        "file_key_prefix": file_key[:6],
                    },
                )
                return file_key

            except (httpx.TransportError, httpx.TimeoutException, RuntimeError) as exc:
                last_error = exc
                if attempt <= _MAX_UPLOAD_RETRIES:
                    logger.warning(
                        "Feishu upload attempt failed, retrying",
                        extra={"attempt": attempt, "error": str(exc)},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
                else:
                    logger.error(
                        "Feishu upload exhausted retries",
                        extra={"attempts": attempt, "error": str(exc)},
                    )

        raise RuntimeError(f"Feishu upload failed after {_MAX_UPLOAD_RETRIES + 1} attempts: {last_error}")
