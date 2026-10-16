"""Persistent user settings.

Stored in ~/.config/musica/settings.json so the choice of quality, format and
last destination survives restarts without needing a database.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("MUSICA_CONFIG_DIR") or (Path.home() / ".config" / "musica"))
CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    # Audio output. "mp3" keeps the widest pendrive/car-stereo compatibility.
    "format": "mp3",
    "quality": "320",  # mp3 VBR-less bitrate in kbps
    # Where to write. Empty means "ask me each time" (the UI falls back to ~/Music/Musica).
    "destination": "",
    # Group playlist downloads into a subfolder named after the playlist.
    "playlist_subfolders": False,
    # "artist_title" writes "Arijit Singh - Tum Hi Ho.mp3"; "title" writes just the song.
    "naming": "artist_title",
    # Skip a track if a file with a near-identical normalized name already exists.
    "skip_name_duplicates": True,
    # Keep the video-ID archive on the drive itself so a second PC inherits the history.
    "archive_on_drive": True,
    # Download and convert on the internal disk, then copy one finished file to the drive.
    "stage_locally": True,
    "search_results": 10,
    "concurrency": 1,
    "cookiefile": "",
    # Car/head-unit friendliness: ID3v2.3 + TYER + ID3v1.1 tail, a clean title in the
    # file name, track numbers in front of the name, capped cover art and a folder.jpg.
    "car_mode": True,
    # What the car's "Genre" view shows. YouTube does not tell us a genre.
    "car_genre": "Music",
    # Prefix "01 - " so a playlist arrives in order on units that sort by file name.
    "number_files": True,
}

# Codecs we allow, mapped to their output extension.
CODEC_EXTENSIONS = {
    "mp3": "mp3",
    "m4a": "m4a",
    "opus": "opus",
    "ogg": "ogg",
    "flac": "flac",
    "wav": "wav",
}

MP3_BITRATES = ("128", "192", "256", "320")

_LOCK = threading.Lock()


def _safe_settings(raw: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULTS)
    for key, value in raw.items():
        if key not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[key], bool):
            settings[key] = bool(value)
        elif isinstance(DEFAULTS[key], int):
            try:
                settings[key] = max(1, int(value))
            except (TypeError, ValueError):
                pass
        else:
            settings[key] = "" if value is None else str(value)

    settings["format"] = settings["format"] if settings["format"] in CODEC_EXTENSIONS else "mp3"
    settings["naming"] = settings["naming"] if settings["naming"] in ("artist_title", "title") else "artist_title"
    if not re.fullmatch(r"\d{2,3}", settings["quality"]):
        settings["quality"] = DEFAULTS["quality"]
    settings["search_results"] = min(int(settings["search_results"]), 100)
    settings["concurrency"] = min(int(settings["concurrency"]), 4)
    settings["car_genre"] = settings["car_genre"].strip()[:40] or "Music"
    return settings


def load() -> dict[str, Any]:
    with _LOCK:
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return _safe_settings(raw if isinstance(raw, dict) else {})


def save(settings: dict[str, Any]) -> dict[str, Any]:
    clean = _safe_settings(settings)
    with _LOCK:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)
    return clean


def update(partial: dict[str, Any]) -> dict[str, Any]:
    settings = load()
    settings.update(partial or {})
    return save(settings)
