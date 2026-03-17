import asyncio
import os
import json

import httpx
import pytest
from dotenv import dotenv_values


RUN_IT = os.getenv("RUN_INTEGRATION_TESTS") == "1"


@pytest.mark.skipif(not RUN_IT, reason="Set RUN_INTEGRATION_TESTS=1 to run live bridge integration tests")
def test_feishu_pipeline():
    asyncio.run(_run_feishu_pipeline())


async def _run_feishu_pipeline():
    url = "http://127.0.0.1:5200/tts"
    cfg = dotenv_values(".env")
    token = cfg.get("INTERNAL_TTS_TOKEN") or ""
    if not token:
        pytest.skip("INTERNAL_TTS_TOKEN missing in .env")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Test case 1: Feishu channel (should return upload JSON envelope)
    payload = {
        "text": "Hello Feishu! ❤️",
        "channel": "feishu",
        "format": "wav",
    }

    print("Testing Feishu pipeline...")
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        assert resp.status_code == 200
        assert "application/json" in (resp.headers.get("content-type") or "")
        data = resp.json()
        assert data.get("msg_type") == "audio"

    # Test case 2: Telegram channel (should return binary audio)
    payload = {
        "text": "Hello Telegram! 😊",
        "channel": "telegram",
        "format": "wav",
    }

    print("\nTesting Telegram pipeline...")
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Content-type: {resp.headers.get('content-type')}")
        print(f"Size: {len(resp.content)}")
        assert resp.status_code == 200
        assert "audio/" in (resp.headers.get("content-type") or "")
        assert len(resp.content) > 1000


if __name__ == "__main__":
    asyncio.run(_run_feishu_pipeline())
