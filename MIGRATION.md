# MIGRATION.md — QWEN-TTS-BRIDGE

## Goal

This note documents how to migrate **the current host production setup** from:

- `systemd + local venv_bridge + 127.0.0.1:5200`

into:

- `Docker / docker-compose`

without breaking the active OpenClaw integration.

---

## Current production baseline

The current live service on this host is:

- Service: `tts-bridge.service`
- Runtime: `venv_bridge/bin/uvicorn`
- Bind: `127.0.0.1:5200`
- Env file: `Graces_Tools/QWEN-TTS-BRIDGE/.env`
- Current callers (important): local OpenClaw workflows expect the bridge on `http://127.0.0.1:5200/tts`

Before any migration, verify:

```bash
systemctl status tts-bridge
curl http://127.0.0.1:5200/health
```

---

## Main migration risk

The Docker examples in this repo default to **port 8000**, while the current production setup uses **port 5200**.

If Docker is started with the default examples **without adjusting port alignment**, OpenClaw may continue calling `127.0.0.1:5200` while the container only exposes `8000`, making TTS appear broken even though the container is healthy.

---

## Recommended migration strategy

### Option A — safest: keep external contract unchanged

Keep OpenClaw and all callers using the same endpoint:

- external target remains `127.0.0.1:5200`

Use Docker only as an internal runtime replacement.

Recommended Docker mapping:

```yaml
ports:
  - "5200:8000"
```

This is the preferred path for this host because it avoids changing the OpenClaw caller side.

### Option B — change callers later

Run Docker on `8000` first, then update every caller to use:

- `http://127.0.0.1:8000/tts`

This is riskier unless you explicitly audit every caller and integration.

---

## Pre-migration checklist

Before switching:

1. Confirm `.env` contains all required secrets.
2. Confirm `ffmpeg` is available inside the container image.
3. Confirm Docker port mapping matches the expected caller contract.
4. Confirm health endpoint path remains `/health`.
5. Confirm Telegram / Feishu / OpenClaw voice workflows are tested after cutover.
6. Confirm there is only **one active runtime** after cutover (avoid systemd + Docker both serving simultaneously unless intentional).

---

## Suggested cutover sequence

### 1. Build container first

```bash
docker compose build
```

### 2. Start container on the intended final port mapping

Recommended for this host:

```yaml
ports:
  - "5200:8000"
```

Then:

```bash
docker compose up -d
curl http://127.0.0.1:5200/health
```

### 3. Verify TTS before disabling systemd

Use a real `/tts` request and confirm audio is returned.

### 4. Stop old runtime only after container validation

```bash
systemctl stop tts-bridge
systemctl disable tts-bridge
```

Only do this after confirming Docker path works.

### 5. Re-test full OpenClaw caller path

Validate:

- local bridge synthesis
- Telegram voice bubble flow
- Feishu native bubble flow (if enabled)
- any automation depending on `/tts`

---

## Rollback plan

If Docker migration fails:

```bash
docker compose down
systemctl enable tts-bridge
systemctl start tts-bridge
curl http://127.0.0.1:5200/health
```

Rollback is simple as long as the original systemd unit and `venv_bridge` remain intact.

---

## Practical recommendation for this host

For **this machine**, the best near-term plan is:

- keep `systemd + venv_bridge` as production
- treat Docker as a prepared alternate runtime
- if migrating later, map Docker to `5200:8000` so OpenClaw callers do not need to change

---

## After migration, re-check these exact items

```bash
docker compose ps
curl http://127.0.0.1:5200/health
```

And for current local integration:

```bash
systemctl status tts-bridge
journalctl -u tts-bridge -n 100 --no-pager
```

If Docker is now production, replace the systemd checks with container logs and keep the endpoint contract unchanged if possible.
