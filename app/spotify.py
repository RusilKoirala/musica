"""Spotify links -> the same track items the YouTube preview uses.

Spotify never serves the audio, so a Spotify item is only a precise description of a song
(clean title, artists, album, length, cover). Its ``url`` is a ``ytsearch:`` query that
``ytdl.download_item`` turns into the best-matching YouTube upload at download time, so the
whole queue - progress, retry, skip rules - works unchanged.

The item ``id`` is the Spotify track id. Once a song is saved it is written to the drive's
archive as ``spotify <id>``, so syncing the same playlist again downloads only new songs.
"""

from __future__ import annotations

import re
import threading
from typing import Any

SPOTIFY_URL_RE = re.compile(
    r"(?:https?://)?open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?(track|album|playlist)/([A-Za-z0-9]+)",
    re.I,
)

_INIT_LOCK = threading.Lock()
_ready = False


def is_spotify(text: str) -> bool:
    return bool(SPOTIFY_URL_RE.search(text or ""))


def _client() -> None:
    """spotdl's client is a process-wide singleton that refuses a second init."""
    global _ready
    with _INIT_LOCK:
        if _ready:
            return
        from spotdl.utils.config import DEFAULT_CONFIG
        from spotdl.utils.spotify import SpotifyClient

        try:
            SpotifyClient.init(DEFAULT_CONFIG["client_id"], DEFAULT_CONFIG["client_secret"], no_cache=True)
        except Exception as exc:  # already initialised elsewhere is fine
            if "already been initialized" not in str(exc):
                raise
        _ready = True


_SIZE_300 = "ab67616d00001e02"
_SIZE_640 = "ab67616d0000b273"


def cover_for(track_id: str) -> str | None:
    """A track's 640x640 album cover from Spotify's public oEmbed endpoint.

    spotdl's playlist reader gets tracks with an empty ``album`` (no images, no name), so
    playlist songs would otherwise have no cover at all. oEmbed needs no login and answers
    in a fraction of a second; its 300px URL shares the image id with the 640px one.
    """
    if not track_id:
        return None
    import json
    import urllib.request

    url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            thumbnail = json.load(response).get("thumbnail_url")
    except Exception:
        return None
    return thumbnail.replace(_SIZE_300, _SIZE_640) if thumbnail else None


def _fill_covers(items: list[dict[str, Any]]) -> None:
    missing = [item for item in items if not item.get("thumbnail")]
    if not missing:
        return
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=16) as pool:
        for item, cover in zip(missing, pool.map(lambda item: cover_for(item["id"]), missing)):
            item["thumbnail"] = cover


def search_query(artists: list[str], title: str) -> str:
    return f"{', '.join(artists)} - {title} audio"


def _item(song: Any, list_name: str | None, position: int | None) -> dict[str, Any]:
    artists = list(song.artists or []) or [song.artist or ""]
    return {
        "id": song.song_id,
        "url": "ytsearch:" + search_query(artists, song.name),
        "title": song.name,
        "artist": song.artist or artists[0],
        "duration": song.duration,
        "thumbnail": song.cover_url,
        "playlist": list_name,
        "playlist_index": position,
        "album": song.album_name or "",
        "year": str(song.year or "")[:4],
        "uploader": ", ".join(artists),
        "source": "spotify",
    }


def resolve(text: str) -> dict[str, Any]:
    """Metadata-only preview of a Spotify track, album or playlist link."""
    match = SPOTIFY_URL_RE.search(text or "")
    if not match:
        raise ValueError("That is not a Spotify track, album or playlist link.")
    kind, spotify_id = match.group(1).lower(), match.group(2)
    url = f"https://open.spotify.com/{kind}/{spotify_id}"
    _client()

    from spotdl.types.album import Album
    from spotdl.types.playlist import Playlist
    from spotdl.types.song import Song

    if kind == "track":
        songs, title = [Song.from_url(url)], None
    else:
        meta, songs = (Album if kind == "album" else Playlist).get_metadata(url)
        title = meta.get("name") or ("Album" if kind == "album" else "Playlist")

    items = [
        _item(song, title, index if title else None)
        for index, song in enumerate(songs, start=1)
        if song and song.song_id
    ]
    _fill_covers(items)
    return {
        "kind": "spotify",
        "query": url,
        "url": url,
        "playlist_title": title,
        "is_playlist": title is not None,
        "items": items,
    }
