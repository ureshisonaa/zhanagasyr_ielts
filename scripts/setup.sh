#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ffprobe >/dev/null
command -v ffmpeg >/dev/null
python -m venv .venv
.venv/bin/pip install --disable-pip-version-check --no-cache-dir -r requirements.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check
