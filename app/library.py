"""What already lives on the drive.

Two independent skip mechanisms, because they catch different cases:

1. **Same video** - yt-dlp's ``download_archive`` file. One line per downloaded ID.
   We keep it on the pendrive (``.musica_archive.txt``) so a second computer inherits
   the history, and so re-syncing a saved playlist downloads only the new songs.
2. **Same song, different upload** - the IDs differ, so the archive is useless. We scan
   the destination for audio files, normalize their names, and fuzzy-match.

Scanning a pendrive is slow (many small files over USB), so the index is cached per
destination with a short TTL and refreshed eagerly whenever we write a file there.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .sanitize import is_audio_file, key_granularity, normalize_key, similar

ARCHIVE_FILENAME = ".musica_archive.txt"

# How long a cached name index stays trusted, and how deep we look for songs.
INDEX_TTL_SECONDS = 120.0
MAX_SCAN_DEPTH = 3
# Above this similarity we call it the same recording; duration narrows false positives.
NAME_MATCH_THRESHOLD = 0.86
DURATION_TOLERANCE_SECONDS = 12.0

_STATE_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")


def archive_path(destination: str | Path, on_drive: bool = True) -> Path:
    root = Path(str(destination)).expanduser()
    if on_drive:
        return root / ARCHIVE_FILENAME
    from .config import CONFIG_DIR

    # Keyed by destination so one off-drive archive still remembers many pendrives.
    slug = "".join(ch if ch.isalnum() else "_" for ch in str(root.resolve()))[:60]
    return CONFIG_DIR / "archives" / f"{slug}.txt"


def read_archive_ids(path: Path) -> set[str]:
    """Every ID or URL recorded by yt-dlp, as the trailing token of each line."""
    ids: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ids
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        token = parts[-1]
        if "/" in token:
            token = token.rstrip("/").rsplit("/", 1)[-1]
            token = token.split("?", 1)[0]
        if token:
            ids.add(token.lower())
    return ids


def in_archive(ids: set[str], video_id: str) -> bool:
    return bool(video_id) and video_id.lower() in ids


class NameIndex:
    """Normalized names of the audio files under one destination."""

    def __init__(self, root: Path):
        self.root = root
        self.entries: list[dict[str, Any]] = []
        self.built_at = 0.0
        self.ready = False
        self.lock = threading.Lock()

    def build(self, force: bool = False) -> "NameIndex":
        with self.lock:
            if self.ready and not force and (time.time() - self.built_at) < INDEX_TTL_SECONDS:
                return self
            self.entries = []
            if self.root.is_dir():
                self._scan(self.root, depth=0)
            self.built_at = time.time()
            self.ready = True
            return self

    def _scan(self, directory: Path, depth: int) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            name = child.name
            if name.startswith(".") or name.endswith(_STATE_SUFFIXES):
                continue
            if child.is_dir():
                if depth + 1 < MAX_SCAN_DEPTH:
                    self._scan(child, depth + 1)
            elif child.is_file() and is_audio_file(child):
                self.entries.append(
                    {
                        "path": child,
                        "display": child.relative_to(self.root).as_posix() if _is_under(child, self.root) else name,
                        "key": normalize_key(child.stem),
                        "duration": None,  # cheap to fill in only when a candidate matches
                        "mtime": _mtime(child),
                    }
                )

    def add(self, path: Path) -> None:
        """Track a file we just wrote, so the next item sees it without a rescan."""
        with self.lock:
            if not (path.exists() and is_audio_file(path)):
                return
            self.entries.append(
                {
                    "path": path,
                    "display": path.relative_to(self.root).as_posix() if _is_under(path, self.root) else path.name,
                    "key": normalize_key(path.stem),
                    "duration": None,
                    "mtime": _mtime(path),
                }
            )

    def find_duplicate(self, title: str, artist: str = "", duration: float | None = None) -> dict[str, Any] | None:
        """The closest existing file, if one is clearly the same recording."""
        self.build()
        key = normalize_key(title, artist)
        if not key:
            return None

        best: dict[str, Any] | None = None
        best_score = 0.0
        for entry in self.entries:
            score = similar(key, entry["key"])
            if score <= best_score or score < NAME_MATCH_THRESHOLD:
                continue
            # Only spend time reading a header when the names already collide.
            length = _audio_duration(entry["path"])
            if duration and length and abs(float(duration) - length) > DURATION_TOLERANCE_SECONDS:
                continue
            # A one-word match ("hello" vs "hello world") is not enough on its own: it has
            # to be backed by a matching duration, otherwise we would skip a different song.
            if min(key_granularity(key), key_granularity(entry["key"])) <= 1:
                if not (duration and length and abs(float(duration) - length) <= DURATION_TOLERANCE_SECONDS):
                    continue
            best, best_score = entry, score

        if best is None:
            return None
        return {"path": str(best["path"]), "display": best["display"], "score": round(best_score, 3)}


_INDEXES: dict[str, NameIndex] = {}
_INDEX_LOCK = threading.Lock()


def index_for(destination: str | Path) -> NameIndex:
    root = Path(str(destination)).expanduser().resolve()
    with _INDEX_LOCK:
        index = _INDEXES.get(str(root))
        if index is None:
            index = NameIndex(root)
            _INDEXES[str(root)] = index
        return index


def invalidate(destination: str | Path) -> None:
    root = str(Path(str(destination)).expanduser().resolve())
    with _INDEX_LOCK:
        _INDEXES.pop(root, None)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _audio_duration(path: Path) -> float | None:
    """Read the length straight from the container; ffprobe is only a fallback."""
    try:
        from mutagen import File as MutagenFile

        media = MutagenFile(str(path))
        if media is not None and getattr(media, "info", None) is not None:
            length = float(media.info.length or 0)
            return length or None
    except Exception:
        pass
    return None
