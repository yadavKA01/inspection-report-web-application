#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
cd "$(dirname "$0")/backend"
echo "[start] cwd=$(pwd) PORT=${PORT:-unset}"
exec uvicorn serve_balloon:app --host 0.0.0.0 --port "${PORT:-10000}"
