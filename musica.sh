#!/usr/bin/env bash
# Musica launcher: creates the virtualenv on first run, then starts the app.
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "First run: creating the virtualenv and installing dependencies ..."
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv
    uv pip install --python "$PY" fastapi "uvicorn[standard]" yt-dlp psutil mutagen requests
  else
    python3 -m venv .venv
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install fastapi "uvicorn[standard]" yt-dlp psutil mutagen requests
  fi
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Warning: ffmpeg is not on PATH. Downloads will work but MP3 conversion will fail." >&2
  echo "         macOS: brew install ffmpeg    Ubuntu: sudo apt install ffmpeg" >&2
fi

exec "$PY" -m app "$@"
