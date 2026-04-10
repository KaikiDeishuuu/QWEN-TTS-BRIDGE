# Deployment Guide

Operational runbook for deploying `QWEN-TTS-BRIDGE` in Docker.

## 1) Requirements

- Docker Engine + Docker Compose plugin (Compose v2).
- Network egress to `dashscope.aliyuncs.com` (for realtime websocket).
- Open inbound TCP port: `8000` (or your mapped host port).
- Host resources (baseline):
  - 1 vCPU+
  - 512MB RAM+
  - enough disk for images/log retention

## 2) Deployment Steps

```bash
git clone <repo-url>
cd QWEN-TTS-BRIDGE
cp .env.example .env
```

Edit `.env` and set at least:

- `DASHSCOPE_API_KEY`
- `INTERNAL_TTS_TOKEN`

Start service:

```bash
docker compose up -d --build
```

Validate:

```bash
curl http://127.0.0.1:8000/health
docker compose ps
```

## 3) Production Notes

### Reverse proxy

Recommended to place Nginx/Traefik in front for:
- TLS termination
- request logging
- upstream timeouts and buffering controls

Example Nginx upstream target: `http://tts-bridge:8000` (internal Docker network) or localhost mapped port.

### TLS and auth

- Do not expose `/tts` publicly without perimeter controls.
- Keep `INTERNAL_TTS_TOKEN` secret and rotate periodically.
- Prefer HTTPS between callers and proxy edge.

### Scaling / resources

- Service is stateless; horizontal scaling is possible.
- Tune `MAX_CONCURRENT_REQUESTS` based on CPU/network limits and provider quotas.

## 4) Troubleshooting

### Container exits immediately

Likely missing required env vars (`DASHSCOPE_API_KEY`, `INTERNAL_TTS_TOKEN`) or invalid typed env values.

Check:

```bash
docker compose logs tts-bridge
```

### Port already in use

If host `8000` is busy, set `PORT` in `.env` (e.g. `PORT=18000`) and recreate container.

```bash
docker compose up -d --build
```

### Healthcheck failing

- Confirm app started: `docker compose logs -f tts-bridge`
- Verify route manually: `curl http://127.0.0.1:<PORT>/health`
- If startup crashes, inspect env and provider connectivity.

### Provider API errors / synthesis failures

- Verify `DASHSCOPE_API_KEY` is valid.
- Verify outbound network access to DashScope websocket endpoint.
- Check bridge logs for fallback reasons (`fallback_to_text`, reason codes).

## 5) Logs & Debugging

```bash
docker compose logs -f
docker compose logs -f tts-bridge
```

Check runtime env visibility inside container venv:

```bash
docker compose exec tts-bridge /opt/venv/bin/python -c "import os; print(os.getenv('DASHSCOPE_API_KEY'))"
docker compose exec tts-bridge /opt/venv/bin/python -c "import os; print(os.getenv('INTERNAL_TTS_TOKEN'))"
```
