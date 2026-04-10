#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec /opt/venv/bin/uvicorn tts_bridge.server:app --host "${HOST}" --port "${PORT}"
