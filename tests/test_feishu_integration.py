import asyncio
import json
import httpx

async def test_feishu_pipeline():
    url = "http://127.0.0.1:5200/tts"
    headers = {
        "Authorization": "Bearer test-token-123",
        "Content-Type": "application/json"
    }
    
    # Test case 1: Feishu channel (should trigger upload attempt)
    # Since we don't have real app_id/app_secret in .env, this should trigger a fallback-to-text or error
    payload = {
        "text": "Hello Feishu! ❤️",
        "channel": "feishu",
        "format": "wav"
    }
    
    print("Testing Feishu pipeline (expected fallback if keys missing)...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

    # Test case 2: Telegram channel (should return binary audio)
    payload = {
        "text": "Hello Telegram! 😊",
        "channel": "telegram",
        "format": "wav"
    }
    
    print("\nTesting Telegram pipeline (expected binary ogg)...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            print(f"Status: {resp.status_code}")
            print(f"Content-type: {resp.headers.get('content-type')}")
            if "audio" in resp.headers.get("content-type", ""):
                print(f"Received binary audio, size: {len(resp.content)} bytes")
            else:
                print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_feishu_pipeline())
