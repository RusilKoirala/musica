
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
   
    "format": "mp3",
    "quality": "320",  
    "destination": "",
    "playlist_subfolders": False,
    "naming": "artist_title",
    "skip_name_duplicates": True,
    "archive_on_drive": True,
    "stage_locally": True,
    "search_results": 10,
    "concurrency": 1,
    "cookiefile": "",
    "car_mode": True,
    "car_genre": "Music",
    "number_files": True,
}


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
