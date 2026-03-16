import logging
import time
import subprocess
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

class FeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.tenant_access_token: Optional[str] = None
        self.token_expiry: float = 0

    async def _ensure_token(self):
        """Ensure we have a valid tenant_access_token."""
        if self.tenant_access_token and time.time() < self.token_expiry:
            return

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu token acquisition failed: {data.get('msg')}")
                
            self.tenant_access_token = data["tenant_access_token"]
            # Expiry usually 2 hours, refresh slightly early
            self.token_expiry = time.time() + data.get("expire", 7200) - 60
            logger.info("Feishu tenant_access_token refreshed")

    def _probe_duration_ms(self, file_path: str) -> int:
        """Best-effort duration probe for opus upload; fallback to 3000ms."""
        try:
            out = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    file_path,
                ],
                text=True,
            ).strip()
            sec = float(out)
            ms = max(1, int(sec * 1000))
            return ms
        except Exception:
            return 3000

    async def upload_audio(self, file_path: str) -> str:
        """
        Upload an opus audio file to Feishu and return file_key.
        Reference: https://open.feishu.cn/document/uAjLw4CM/ukTMzUjL5EzM14SO5MTN/reference/im-v1/file/create
        """
        await self._ensure_token()

        url = "https://open.feishu.cn/open-apis/im/v1/files"
        headers = {
            "Authorization": f"Bearer {self.tenant_access_token}"
        }

        duration_ms = self._probe_duration_ms(file_path)
        files = {
            "file": open(file_path, "rb")
        }
        data = {
            "file_name": "voice.opus",
            "file_type": "opus",
            "duration": str(duration_ms),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
            resp.raise_for_status()
            res = resp.json()

            if res.get("code") != 0:
                raise RuntimeError(f"Feishu file upload failed: {res.get('msg')}")

            file_key = res["data"]["file_key"]
            logger.info(f"Feishu opus uploaded successfully, key: {file_key[:6]}...")
            return file_key
