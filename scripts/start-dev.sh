#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python manage.py expire_attempts --loop &
expiry_pid=$!
.venv/bin/python manage.py runserver "${DEV_BIND:-0.0.0.0:8000}" --noreload &
web_pid=$!
cleanup() { kill "$expiry_pid" "$web_pid" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 0' INT TERM
wait -n "$web_pid" "$expiry_pid"
