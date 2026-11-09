# The Musica API: FastAPI + yt-dlp + ffmpeg.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv
# yt-dlp needs a JavaScript runtime for YouTube's signature checks.
COPY --from=denoland/deno:bin-2.4.3 /deno /usr/local/bin/deno

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
# --seed keeps pip in the venv, which the "Update yt-dlp" button uses.
RUN uv venv --seed /opt/venv \
 && uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
 && uv pip install --no-cache -r /tmp/requirements.txt

# Lets yt-dlp fetch PO tokens from the pot container, which servers need for YouTube.
RUN uv pip install --no-cache bgutil-ytdlp-pot-provider==2.0.2

# Spotify links. Set --build-arg SPOTIFY=0 to leave it out.
ARG SPOTIFY=1
RUN if [ "$SPOTIFY" = "1" ]; then \
      uv pip install --no-cache --no-deps spotdl \
   && uv pip install --no-cache spotipy rapidfuzz python-slugify pykakasi beautifulsoup4 \
        syncedlyrics ytmusicapi soundcloud-v2 platformdirs rich; \
    fi

COPY app ./app

ENV MUSICA_ROOT=/music \
    MUSICA_CONFIG_DIR=/data \
    MUSICA_ALLOW_FORMAT=0

VOLUME ["/music", "/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["python", "-m", "app", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
