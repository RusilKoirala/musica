"""Everything that touches yt-dlp.

Two jobs:

* **resolve** - turn a pasted link, playlist link, or plain song name into a list of
  track previews, using ``extract_flat`` so we never touch a single audio byte for it.
* **download_item** - convert one track to the user's codec, embed the cover and the
  tags, and record its ID in the on-drive archive.

We download one item per call rather than handing yt-dlp a whole playlist. That keeps
our queue, our per-file duplicate rules, and our progress reporting honest: a broken
video fails alone instead of silently ending the run (which is what a global
``ignoreerrors: True`` would do).
"""

from __future__ import annotations

import glob
import functools
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from . import config, library
from .drives import IS_WINDOWS
from .sanitize import ILLEGAL_CHARS, sanitize, sanitize_parts, strip_noise

_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_PLAYLIST_URL_RE = re.compile(r"(/playlist\?|&list=|/videos|/sets/|/album/|/playlist/)", re.IGNORECASE)

# YouTube answers the default `web` client with HTTP 403 on the audio stream once an IP is
# rate- or datacenter-flagged - the "Sign in to confirm you're not a bot" family, which is
# exactly what made every track in a queue fail with a useless message. The `android`
# client serves the same audio unthrottled; keeping `web` second covers any format android
# omits. Verified on the blocked machine: android,web -> full 320k MP3 where web alone 403s.
YOUTUBE_CLIENTS = ["android", "web"]

# Only these containers take cover art through ffmpeg's metadata path reliably.
_ART_FRIENDLY = {"mp3", "m4a", "aac", "opus", "ogg", "oga", "flac", "mka"}

_PROGRESS_CALLBACK = Callable[[dict[str, Any]], None]


def ffmpeg_available() -> dict[str, Any]:
    path = shutil.which("ffmpeg")
    if not path:
        return {"ok": False, "path": None, "version": None}
    version = None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10).stdout
        first = out.splitlines()[0] if out else ""
        version = first.replace("ffmpeg version ", "").split()[0] or first
    except Exception:
        pass
    return {"ok": True, "path": path, "version": version}


def ytdlp_version() -> str:
    return getattr(yt_dlp.version, "__version__", "unknown")


@functools.lru_cache(maxsize=1)
def js_runtime() -> str | None:
    """Pick the JavaScript runtime yt-dlp should borrow, or None if there is none.

    Recent YouTube only hands over a usable player response once yt-dlp can run the
    page's own signature maths, so without a runtime every lookup comes back as
    "Sign in to confirm you're not a bot". deno is yt-dlp's default; node and bun
    work too and are far more common on a laptop.
    """
    return next((name for name in ("deno", "node", "bun") if shutil.which(name)), None)


def classify(text: str) -> tuple[str, str]:
    """('url', link) when it looks like a link, otherwise ('search', cleaned text)."""
    raw = (text or "").strip()
    if _URL_RE.match(raw):
        link = raw if raw.lower().startswith("http") else "https://" + raw
        return "url", link
    return "search", raw


def _base_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "ffmpeg_location": shutil.which("ffmpeg"),
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        },
    }
    runtime = js_runtime()
    if runtime:
        opts["js_runtimes"] = {runtime: {}}
    opts["extractor_args"] = {"youtube": {"player_client": YOUTUBE_CLIENTS}}
    return opts


def _thumbnail_for(info: dict[str, Any]) -> str | None:
    thumbs = info.get("thumbnails") or []
    # Prefer a mid-size jpg: small enough to embed fast, good enough to show.
    ranked = sorted(
        (t for t in thumbs if t.get("url")),
        key=lambda t: abs((t.get("width") or 0) - 480),
    )
    if ranked:
        return ranked[0]["url"]
    video_id = info.get("id") or ""
    if _YOUTUBE_ID_RE.match(video_id):
        return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    return None


def _clean_item(info: dict[str, Any], playlist_title: str | None) -> dict[str, Any]:
    url = info.get("webpage_url") or info.get("url") or ""
    if url.startswith("//"):
        url = "https:" + url
    video_id = info.get("id") or ""
    if not url and _YOUTUBE_ID_RE.match(video_id):
        url = f"https://www.youtube.com/watch?v={video_id}"
    artist = info.get("artist") or info.get("uploader") or info.get("channel") or info.get("creator") or ""
    index = info.get("playlist_index")
    return {
        "id": video_id,
        "url": url,
        "title": info.get("title") or sanitize(video_id or "untitled", fallback="untitled"),
        "artist": artist,
        "duration": info.get("duration"),
        "thumbnail": _thumbnail_for(info),
        "playlist": playlist_title,
        "playlist_index": index,
        "uploader": info.get("uploader") or info.get("channel") or "",
        "upload_date": info.get("upload_date") or info.get("release_timestamp") or "",
    }


def resolve(text: str, limit: int = 10, prefer_playlist: bool | None = None) -> dict[str, Any]:
    """Metadata-only preview of a link, a playlist, or a search query."""
    kind, value = classify(text)
    opts = _base_opts()
    opts.update(
        {
            "skip_download": True,
            # "in_playlist" keeps full metadata for a lone video and is fast inside playlists.
            "extract_flat": "in_playlist",
            # A single watch?v= link with a list= tail must not pull the whole queue in.
            "noplaylist": not _PLAYLIST_URL_RE.search(value) if prefer_playlist is None else not prefer_playlist,
        }
    )
    if kind == "search":
        target = f"ytsearch{max(1, min(int(limit), 100))}:{value}"
    else:
        target = value

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    if info is None:
        return {"kind": kind, "query": value, "playlist_title": None, "items": []}

    # A ytsearch: result is technically a playlist, but it must not become a folder name.
    is_playlist = kind == "url" and info.get("_type") == "playlist"
    playlist_title = info.get("title") if is_playlist else None
    raw_entries = info.get("entries")
    if raw_entries is None:  # a bare video
        entries = [info]
    else:
        entries = [e for e in raw_entries if e]

    items = [_clean_item(entry, playlist_title) for entry in entries]
    items = [item for item in items if item.get("url")]
    return {
        "kind": kind,
        "query": value,
        "playlist_title": playlist_title,
        "is_playlist": is_playlist,
        "url": info.get("webpage_url") or (value if kind == "url" else None),
        "items": items,
    }


def _requested_format(settings: dict[str, Any]) -> str:
    codec = settings.get("format", "mp3")
    if codec == "flac":
        # Only take a truly lossless source if the site offers one; otherwise best audio.
        return "bestaudio/best"
    return "bestaudio/best"


def output_stem(item: dict[str, Any], settings: dict[str, Any]) -> str:
    """A finished, filesystem-safe file stem built from the preview we showed the user.

    Handing ``%(title)s`` to yt-dlp looked tidy, but real YouTube metadata is not: a
    "artist" field can be a wall of featured singers and a Hindi title is three bytes per
    letter, which produced names longer than the volume allows and the write died with
    ENAMETOOLONG. Naming it here puts the length cap under our control, in bytes.

    Car players are the reason for the last two touches. A head unit that cannot read the
    tags shows the *file name* as the track name, so the name has to read like a song, and
    a unit that lists by name needs the track number in front or a playlist arrives shuffled.
    """
    title = str(item.get("title") or item.get("id") or "track")
    artist = str(item.get("artist") or item.get("uploader") or "").strip()
    naming = settings.get("naming", "artist_title")
    if settings.get("car_mode", True):
        title = strip_noise(title) or title
    # Do not repeat the song when the channel name already is the song's own title.
    if naming == "artist_title" and artist and artist.lower() != title.lower():
        title = f"{artist} - {title}"
    stem = sanitize(title, fallback="track", max_length=120, max_bytes=170)
    index = item.get("playlist_index")
    if settings.get("car_mode", True) and settings.get("number_files", True) and isinstance(index, int) and index > 0:
        stem = f"{index:02d} - {stem}"
    return stem


def build_download_opts(
    root: Path,
    settings: dict[str, Any],
    *,
    archive: Path | None = None,
    item: dict[str, Any] | None = None,
    on_progress: _PROGRESS_CALLBACK | None = None,
    logger: Any = None,
) -> dict[str, Any]:
    """yt-dlp options for writing one finished track into ``root``."""
    codec = settings.get("format", "mp3")
    quality = str(settings.get("quality", "320"))

    folder_parts: list[str] = []
    if item and settings.get("playlist_subfolders", False) and item.get("playlist"):
        folder_parts = sanitize_parts(item["playlist"])
    target_dir = root.joinpath(*folder_parts) if folder_parts else root

    if item:
        # A literal name: no template fields left to explode.
        outtmpl = str(target_dir / f"{output_stem(item, settings)}.%(ext)s")
    else:
        # Metadata-free callers (tests) still get a sensible template.
        stem = "%(artist,uploader,title)s - %(title)s" if settings.get("naming") != "title" else "%(title)s"
        outtmpl = str(target_dir / f"{stem}.%(ext)s")

    extract: dict[str, Any] = {
        "key": "FFmpegExtractAudio",
        "preferredcodec": codec,
        # Lossless and container-native codecs ignore a bitrate, and yt-dlp wants None there.
        "preferredquality": quality if codec in {"mp3", "m4a", "aac", "opus", "ogg", "oga"} else None,
    }
    if codec == "wav":
        extract["preferredquality"] = "16"

    want_art = codec in _ART_FRIENDLY
    postprocessors: list[dict[str, Any]] = [
        extract,
        {"key": "FFmpegMetadata", "add_metadata": True},
    ]
    if want_art:
        # "convert_thumbnails" only exists as a command-line flag, so in library mode the
        # converter has to be queued by hand. Without it yt-dlp embeds a 1280x720 PNG and
        # adds a megabyte of cover art to every track; ID3 and MP4 both want a JPEG.
        postprocessors.insert(0, {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    opts = _base_opts()
    opts.update(
        {
            "format": _requested_format(settings),
            "outtmpl": {"default": outtmpl, "windowsfilenames": True},
            # windowsfilenames: True is the strongest portable guarantee for FAT32/exFAT
            # and costs us nothing on macOS or Linux.
            "windowsfilenames": True,
            "trim_file_name": 200,
            "postprocessors": postprocessors,
            "writethumbnail": want_art,
            "download_archive": str(archive) if archive else None,
            "noplaylist": True,
            "ignoreerrors": False,  # one item per call: surface the real failure
            "overwrites": True,
            "concurrent_fragment_downloads": 3,
        }
    )
    if settings.get("cookiefile"):
        opts["cookiefile"] = str(settings["cookiefile"])
    if on_progress:
        opts["progress_hooks"] = [on_progress]
    if logger:
        opts["logger"] = logger
    return opts


class _CaptureLogger:
    """Keep yt-dlp's own error text so we can show *why* a track failed."""

    def __init__(self, sink: list[str]):
        self.sink = sink

    def debug(self, msg: str) -> None:  # yt-dlp routes info through debug()
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.sink.append(str(msg))

    def error(self, msg: str) -> None:
        self.sink.append(str(msg))


def _final_path(opts: dict[str, Any], info: dict[str, Any], codec: str) -> Path | None:
    """Where yt-dlp ended up writing, once the audio has been extracted."""
    with yt_dlp.YoutubeDL({"quiet": True, "outtmpl": opts["outtmpl"]}) as helper:
        prepared = helper.prepare_filename(info)
    stem = Path(prepared).with_suffix("").as_posix()
    candidate = Path(f"{stem}.{config.CODEC_EXTENSIONS.get(codec, codec)}")
    if candidate.is_file():
        return candidate
    # ffmpeg sometimes picks a slightly different extension (.m4a vs .mp4); look nearby.
    parent = Path(stem).parent
    try:
        # A title is full of glob magic ("[]", "*"), so escape the stem before matching.
        matches = sorted(
            (p for p in parent.glob(glob.escape(Path(stem).name) + ".*") if library.is_audio_file(p)),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
    except OSError:
        return None
    return matches[0] if matches else None


def _drop_leftovers(final: Path) -> None:
    """Cover art is embedded by now; sidecar images and .part files are litter."""
    for sibling in final.parent.glob(glob.escape(final.stem) + ".*"):
        if sibling == final:
            continue
        if sibling.suffix.lower() in {".jpg", ".jpeg", ".webp", ".png", ".part", ".ytdl", ".thm"}:
            try:
                sibling.unlink()
            except OSError:
                pass


# yt-dlp rewrites characters Windows forbids into fullwidth look-alikes, so "Song | Live"
# lands as "Song ｜ Live". Nothing about that is illegal on a pendrive, it just looks wrong
# on a car stereo, so we rename once the file is finished. Renaming (rather than reaching
# into yt-dlp's private sanitizer) keeps working when yt-dlp changes underneath us.
# A quote maps to an apostrophe, never back to `"` - that would undo the whole point.
_LOOK_ALIKE = {
    "\uFF02": "'",  # ＂
    "\uFF1C": " ", "\uFF1E": " ", "\uFF1A": " ", "\uFF1F": " ",
    "\uFF5C": " ", "\uFF0A": " ", "\u29F8": " ", "\u29F9": " ",
}


def _tidy_name(path: Path) -> Path:
    name = path.name
    for character, replacement in _LOOK_ALIKE.items():
        name = name.replace(character, replacement)
    name = re.sub(r" {2,}", " ", name).strip()
    # If our rewrite reintroduced something FAT/NTFS cannot store, keep yt-dlp's version.
    if not name or name == path.name or any(character in name for character in ILLEGAL_CHARS):
        return path
    candidate = path.with_name(name)
    if candidate.exists():
        return path  # never overwrite a song we already have
    try:
        path.rename(candidate)
    except OSError:
        return path
    return candidate


# ------------------------------------------------------------------- staging

# Pendrives are slow at many small writes and die badly if pulled mid-transfer, so when
# the destination is a different filesystem we download and convert on the internal disk
# and finish with one big copy. The archive line is only merged once that copy lands,
# so an interrupted run never leaves a "already downloaded" note for a missing file.
PENDING_ARCHIVE_NAME = "pending_archive.txt"


def staging_root() -> Path:
    path = config.CONFIG_DIR / "staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _same_filesystem(a: Path, b: Path) -> bool:
    if IS_WINDOWS:
        drive_a = (Path(a).drive or "").upper()
        drive_b = (Path(b).drive or "").upper()
        return bool(drive_a) and drive_a == drive_b
    walker = a if a.exists() else a.parent
    while not walker.exists():
        walker = walker.parent
    try:
        return os.stat(walker).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def should_stage(destination: Path, settings: dict[str, Any]) -> bool:
    if not settings.get("stage_locally", True):
        return False
    return not _same_filesystem(destination, staging_root())


def _promote(staged: Path, stage_root: Path, destination: Path) -> Path:
    """Copy the finished file onto the target volume, keeping its relative path."""
    try:
        relative = staged.relative_to(stage_root)
    except ValueError:
        relative = Path(staged.name)
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staged, target)
    staged.unlink(missing_ok=True)
    # Leave no empty staging folders behind.
    parent = staged.parent
    while parent != stage_root and parent.exists():
        try:
            next(parent.iterdir())
            break
        except StopIteration:
            try:
                parent.rmdir()
            except OSError:
                break
        except OSError:
            break
        parent = parent.parent
    return target


def _merge_pending_archive(pending: Path, real: Path) -> None:
    if not pending.is_file():
        return
    lines = [line.strip() for line in pending.read_text(errors="replace").splitlines() if line.strip()]
    if not lines:
        return
    real.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if real.is_file():
        try:
            existing = {line.strip() for line in real.read_text(errors="replace").splitlines()}
        except OSError:
            existing = set()
    fresh = [line for line in lines if line not in existing]
    if fresh:
        with real.open("a", encoding="utf-8") as handle:
            for line in fresh:
                handle.write(line + "\n")
    try:
        pending.unlink()
    except OSError:
        pass


def purge_staging(older_than_seconds: int = 3600) -> int:
    """Drop leftovers from an interrupted run, so the SSD does not silently fill up."""
    root = staging_root()
    removed = 0
    # A pending archive from a crashed run would otherwise make this run skip songs that
    # never reached the drive.
    (root / PENDING_ARCHIVE_NAME).unlink(missing_ok=True)
    for path in (root / "out").rglob("*") if (root / "out").is_dir() else []:
        if path.is_file() and path.stat().st_mtime < time.time() - older_than_seconds:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _explain_failure(message: str, detail: str = "") -> str:
    """Turn a raw yt-dlp traceback line into something a person can act on.

    "Nothing could be extracted" told the user nothing while the real cause was a YouTube
    bot-check; the raw string is kept so the specific reason is never lost, but a plain
    hint is appended for the failures that actually have a fix.
    """
    text = (message or "").strip()
    # yt-dlp wraps reasons in "ERROR:" and ANSI colour, none of which belong in the log.
    text = _ANSI_RE.sub("", text).strip()
    detail = _ANSI_RE.sub("", (detail or "")).strip()
    if text.startswith("ERROR:"):
        text = text[len("ERROR:"):].strip()
    raw = f"{text} ({detail})" if detail and detail not in text else text
    low = f"{text} {detail}".lower()
    hint = ""
    if "403" in low or "forbidden" in low or "not a bot" in low or "sign in to confirm" in low:
        hint = " YouTube is throttling this machine; it usually clears in a few minutes, or sign in once via Settings."
    elif "age" in low and ("restrict" in low or "confirm" in low):
        hint = " This video is age-restricted and needs signed-in cookies (Settings → cookie file)."
    elif "no longer available" in low or "unavailable" in low or "private video" in low or "does not exist" in low:
        hint = " That upload has been removed or made private, so pick a different result."
    elif "network" in low or "timed out" in low or "temporary failure" in low or "getaddrinfo" in low:
        hint = " This looks like a connection problem; check the network and retry."
    elif "requested format is not available" in low:
        hint = " Try a different format/quality in Settings, or pick another result."
    return (raw + hint) if raw or hint else "The link could not be downloaded."


SEARCH_PREFIX = "ytsearch:"
MATCH_DURATION_TOLERANCE = 15


def find_match(query: str, duration: float | None = None, candidates: int = 6) -> str | None:
    """The YouTube upload that best fits a song described elsewhere (a Spotify track).

    Search order is YouTube's relevance; the first result whose length is within a few
    seconds of the real song wins, which skips music videos with long intros and hour-long
    loops. Without a known length the top result is used.
    """
    opts = _base_opts()
    opts.update({"skip_download": True, "extract_flat": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{candidates}:{query}", download=False) or {}
    entries = [entry for entry in info.get("entries") or [] if entry and entry.get("id")]
    if not entries:
        return None
    chosen = entries[0]
    if duration:
        close = [e for e in entries if e.get("duration") and abs(float(e["duration"]) - float(duration)) <= MATCH_DURATION_TOLERANCE]
        if close:
            chosen = close[0]
    return f"https://www.youtube.com/watch?v={chosen['id']}"


def _record_spotify(item: dict[str, Any], archive: Path) -> None:
    """Remember a saved Spotify track by its id, so a playlist re-sync skips it."""
    if item.get("source") != "spotify" or not item.get("id"):
        return
    pending = archive.with_name(archive.name + ".spotify")
    pending.write_text(f"spotify {item['id']}\n", encoding="utf-8")
    _merge_pending_archive(pending, archive)


def download_item(
    item: dict[str, Any],
    destination: str | Path,
    settings: dict[str, Any],
    *,
    on_progress: _PROGRESS_CALLBACK | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch, convert, tag and file one track. Never raises: returns a status dict."""
    dest = Path(str(destination)).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    codec = settings.get("format", "mp3")

    real_archive = library.archive_path(dest, bool(settings.get("archive_on_drive", True)))
    staged = should_stage(dest, settings)
    write_root = (staging_root() / "out") if staged else dest
    write_root.mkdir(parents=True, exist_ok=True)

    # yt-dlp records a video id into download_archive the instant the bytes are fetched -
    # before our post-processing (or the slow copy to a stick) is finished. Pointing it at
    # the drive's real archive meant that a 403 or a failed convert left behind a line
    # claiming the song was on the drive when no file ever landed, and every later retry
    # was then silently skipped. So a run only ever writes a scratch archive, merged into
    # the real one after the file has actually been filed. It is purged first so a previous
    # failed attempt can never make yt-dlp skip this one.
    run_archive = staging_root() / PENDING_ARCHIVE_NAME
    run_archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_archive.unlink()
    except OSError:
        pass

    def hook(progress: dict[str, Any]) -> None:
        if on_progress is None:
            return
        status = progress.get("status")
        total = progress.get("total_bytes") or progress.get("total_bytes_estimate") or 0
        done = progress.get("downloaded_bytes") or 0
        on_progress(
            {
                "status": status,
                "downloaded": done,
                "total": total,
                "percent": round(done / total * 100, 1) if total else None,
                "speed": progress.get("speed") or 0,
                "eta": progress.get("eta"),
                "filename": Path(progress["filename"]).name if progress.get("filename") else None,
            }
        )

    opts = build_download_opts(
        write_root,
        settings,
        archive=run_archive,
        item=item,
        on_progress=hook,
        logger=_CaptureLogger(errors),
    )

    url = str(item["url"])
    if url.startswith(SEARCH_PREFIX):
        try:
            url = find_match(url[len(SEARCH_PREFIX):], item.get("duration"))
        except Exception as exc:
            return {"status": "error", "item": item, "error": _explain_failure(str(exc))}
        if not url:
            return {"status": "error", "item": item, "error": "No matching song was found on YouTube."}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # network, age-gate, removed video, ffmpeg missing
        detail = " / ".join(dict.fromkeys(errors[-3:])) if errors else ""
        return {"status": "error", "item": item, "error": _explain_failure(str(exc), detail)}
    if info is None:
        return {
            "status": "error",
            "item": item,
            "error": _explain_failure(" ".join(errors[-3:]) or "Nothing could be extracted from that link."),
        }

    final = _final_path(opts, info, codec)
    if final is None:
        return {
            "status": "error",
            "item": item,
            "error": "The download finished but no audio file was found. ffmpeg may have failed.",
        }

    _drop_leftovers(final)
    final = _tidy_name(final)
    # Tag the file before it leaves the SSD: rewriting ID3 frames over USB is the slowest
    # possible way to touch a pendrive.
    tags = write_tags(final, info, item, settings)

    if staged:
        try:
            final = _promote(final, write_root, dest)
        except OSError as exc:
            return {
                "status": "error",
                "item": item,
                "error": f"The track is ready but the copy to the drive failed: {exc}. "
                f"It is still at {final}, so retry once the drive is mounted.",
            }
    # Only now does this video count as downloaded on the drive: merge the scratch record
    # into the real archive. A run that failed above never reaches here, so it adds nothing.
    _merge_pending_archive(run_archive, real_archive)
    try:
        _record_spotify(item, real_archive)
    except OSError:
        pass  # the name index still catches this song next time

    library.index_for(dest).add(final)

    try:
        size = final.stat().st_size
    except OSError:
        size = 0

    return {
        "status": "done",
        "item": item,
        "path": str(final),
        "relative": final.relative_to(dest).as_posix() if _is_under(final, dest) else final.name,
        "size": size,
        "tags": tags,
        "staged": staged,
    }


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except OSError:  # different drives on Windows
        return False


CAR_ART_MAX_BYTES = 200 * 1024  # a head unit wants a small poster, not a 1280x720 frame
CAR_ART_MAX_EDGE = 600
CAR_ART_DESCRIPTION = "Album cover"


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a JPEG's start-of-frame marker, without any decoder.

    Used to decide whether embedded art is too big for a head unit: a byte cap alone is
    not enough, because YouTube's 1280x720 frame compresses small and still will not
    render nicely on a dashboard screen.
    """
    if data[:2] != b"\xff\xd8":
        return None
    cursor = 2
    while cursor + 9 < len(data):
        if data[cursor] != 0xFF:  # stray byte inside entropy data
            cursor += 1
            continue
        marker = data[cursor + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            cursor += 2
            continue
        length = int.from_bytes(data[cursor + 2:cursor + 4], "big")
        # 0xC0-0xCF are the frame headers, minus the reserved/dlt/C4/C8/CC ones.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[cursor + 5:cursor + 7], "big")
            width = int.from_bytes(data[cursor + 7:cursor + 9], "big")
            return (width, height) if width and height else None
        cursor += 2 + length
    return None


def _fit_cover(data: bytes) -> bytes | None:
    """Scale and recompress cover art until a car screen can decode it cheaply.

    YouTube hands over the video's 1280x720 frame, which embeds fine but renders as a
    letterboxed rectangle on a head unit and bloats every file on the stick. Album art is
    square everywhere else, so fit the longest edge to 600 px and keep the aspect ratio -
    cropping would cut the artist's text off the thumbnail.
    """
    if not data:
        return None
    size = _jpeg_size(data) if data[:2] == b"\xff\xd8" else None
    big_enough = size is None or max(size) <= CAR_ART_MAX_EDGE
    if len(data) <= CAR_ART_MAX_BYTES and big_enough:
        return data
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="musica-art-"))
    source, target = work / "in.jpg", work / "out.jpg"
    try:
        source.write_bytes(data)
        best: bytes | None = None
        for quality in ("3", "5", "7", "9", "11"):
            subprocess.run(
                [
                    ffmpeg, "-y", "-loglevel", "error", "-i", str(source),
                    "-vf", f"scale={CAR_ART_MAX_EDGE}:{CAR_ART_MAX_EDGE}:force_original_aspect_ratio=decrease:flags=lanczos",
                    "-q:v", quality, str(target),
                ],
                capture_output=True, timeout=60, check=True,
            )
            best = target.read_bytes()
            if len(best) <= CAR_ART_MAX_BYTES:
                return best
        return best
    except Exception:
        return None
    finally:
        for scratch in (source, target):
            scratch.unlink(missing_ok=True)
        work.rmdir()


def _cover_frame(tags):
    """The APIC frame mutagen stored, whatever description key it used."""
    for key in list(tags.keys()):
        if key.startswith("APIC"):
            return key, tags[key]
    return None, None


def fetch_cover(url: str | None) -> bytes | None:
    """The album poster a Spotify item points at (a 640x640 JPEG from i.scdn.co)."""
    if not url or not str(url).startswith("https://"):
        return None
    import urllib.request

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(5_000_000)
    except Exception:
        return None
    return data if data[:2] == b"\xff\xd8" else None  # only ever embed a real JPEG


def car_picture(path: Path, tags) -> dict[str, Any]:
    """Make the embedded artwork one a car player will actually show, plus a folder.jpg.

    Two real failure modes we can fix here: an oversized 1280x720 frame some units refuse
    to decode (resized to a small square-ish JPEG), and a unit that ignores ID3 pictures
    entirely (a plain folder.jpg beside the files, which nearly every head unit's file
    browser reads, covers that case).
    """
    notes: dict[str, Any] = {}
    from mutagen.id3 import APIC, PictureType

    key, frame = _cover_frame(tags)
    data = bytes(frame.data) if frame else None
    if frame is not None:
        fitted = _fit_cover(bytes(frame.data))
        if fitted and (len(fitted) != len(frame.data) or frame.mime != "image/jpeg"):
            tags[key] = APIC(
                encoding=0,
                mime="image/jpeg",
                type=PictureType.COVER_FRONT,
                desc=CAR_ART_DESCRIPTION,
                data=fitted,
            )
            notes["art_bytes"] = len(fitted)
            data = fitted

    if data:
        try:
            sidecar = path.parent / "folder.jpg"
            sidecar.write_bytes(data)
            notes["folder_jpg"] = True
        except OSError as exc:
            notes["folder_jpg_error"] = str(exc)
    return notes


def write_tags(path: Path, info: dict[str, Any], item: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Backfill title/artist/album tags that FFmpegMetadata leaves empty.

    YouTube reports no ``artist`` for most uploads, so the ID3 TPE1 frame often ends up
    blank and car stereos show "<unknown>". mutagen only touches text frames here;
    EmbedThumbnail has already written the picture, so the artwork is not duplicated.

    Car mode (on by default) aims at what a head unit really reads: an ID3v2.3 tag, because
    plenty of players still cannot parse 2.4; TYER for the year, because TDRC is a 2.4-only
    frame and gets dropped as unknown; TPE2 so the library can group by artist/album; TCON
    for the genre view; and an ID3v1.1 tail as a last resort for units that read nothing but
    that. A real owner's manual puts it plainly: the radio "displays the file name as the
    track name if the song information is not available" - so the tags and the name both
    have to carry a clean title.
    """
    written: dict[str, Any] = {}
    suffix = path.suffix.lower()
    if suffix not in {".mp3", ".m4a", ".aac", ".mp4"}:
        return written

    car = bool(settings.get("car_mode", True))
    # Spotify's square album poster beats the YouTube video frame; YouTube's stays as the
    # fallback when the poster cannot be fetched.
    cover = None
    if item.get("source") == "spotify":
        from . import spotify

        cover = fetch_cover(item.get("thumbnail")) or fetch_cover(spotify.cover_for(str(item.get("id") or "")))
    if item.get("source") == "spotify":
        # Spotify's metadata is the real thing; the YouTube upload's is a guess.
        info = {
            "title": item.get("title"),
            "artist": item.get("uploader") or item.get("artist"),
            "album": item.get("album"),
            "release_date": item.get("year"),
            "playlist_index": item.get("playlist_index"),
        }
    raw_title = str(info.get("title") or item.get("title") or path.stem)
    if car:
        raw_title = strip_noise(raw_title) or raw_title
    title = sanitize(raw_title, max_length=120)
    artist = str(info.get("artist") or info.get("album_artist") or item.get("artist") or item.get("uploader") or "").strip()
    album = str(info.get("album") or item.get("playlist") or "Musica").strip() or "Musica"
    date = str(info.get("release_date") or info.get("upload_date") or "")[:4]
    track = info.get("playlist_index") or item.get("playlist_index") or 1
    genre = str(settings.get("car_genre") or "").strip() or "Music"

    try:
        if suffix == ".mp3":
            from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, TYER, ID3NoHeaderError, PictureType

            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            if cover:
                tags.delall("APIC")
                tags.add(APIC(encoding=0, mime="image/jpeg", type=PictureType.COVER_FRONT,
                              desc=CAR_ART_DESCRIPTION, data=cover))
                written["cover"] = "spotify"
            year_class, year_key = (TYER, "TYER") if car else (TDRC, "TDRC")
            frames = {
                "TIT2": (TIT2, title),
                "TPE1": (TPE1, artist or title),
                "TPE2": (TPE2, artist or "Musica"),
                "TALB": (TALB, album),
                "TRCK": (TRCK, str(track)),
                year_key: (year_class, date),
                "TCON": (TCON, genre),
            }
            if car:
                # ffmpeg may already have written the 2.4-style TDRC. Inside a 2.3 tag a car
                # reads it as nothing at all, so harvest the year from it, then drop it.
                if not date:
                    legacy = tags.get("TDRC") or tags.get("TYER")
                    if legacy is not None and legacy.text:
                        frames["TYER"] = (TYER, str(legacy.text[0])[:4])
                tags.delall("TDRC")
            for frame_id, (frame_class, value) in frames.items():
                if not value:
                    continue
                if frame_id in tags and str(tags[frame_id].text[0] if tags[frame_id].text else "") == str(value):
                    continue
                tags[frame_id] = frame_class(encoding=3, text=str(value))
                written[frame_id] = str(value)
            if car:
                written.update(car_picture(path, tags))
            elif cover:
                try:
                    (path.parent / "folder.jpg").write_bytes(cover)
                except OSError:
                    pass
            # v1=2 leaves an ID3v1.1 tail as well: some units only ever read that one.
            tags.save(path, v2_version=3, v1=2 if car else 1)
        else:
            from mutagen.mp4 import MP4, MP4Cover

            tags = MP4(path)
            if cover:
                tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
                written["cover"] = "spotify"
            values = {
                "\xa9nam": [title],
                "\xa9ART": [artist or title],
                "aART": [artist or "Musica"],
                "\xa9alb": [album],
                "\xa9gen": [genre],
            }
            if date:
                values["\xa9day"] = [date]
            for key, value in values.items():
                if tags.get(key) != value:
                    tags[key] = value
                    written[key] = value[0]
            track_number = int(track) if str(track).isdigit() else None
            if track_number:
                tags["trkn"] = [(track_number, 0)]
            tags.save(path)
    except Exception as exc:  # a bad tag must never lose a finished file
        written["error"] = str(exc)
    return written


UPDATE_LOCK = threading.Lock()


def self_update() -> dict[str, Any]:
    """`pip install -U yt-dlp` in our own interpreter, since YouTube changes constantly."""
    import sys

    before = ytdlp_version()
    with UPDATE_LOCK:
        command = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "before": before, "after": ytdlp_version(), "error": str(exc)}

        if result.returncode != 0:
            return {
                "ok": False,
                "before": before,
                "after": ytdlp_version(),
                "error": (result.stderr or result.stdout)[-800:],
            }

        # Drop every cached yt_dlp module so the new version is the one we use next.
        import importlib

        for module in [name for name in sys.modules if name == "yt_dlp" or name.startswith("yt_dlp.")]:
            del sys.modules[module]
        globals()["yt_dlp"] = importlib.import_module("yt_dlp")
        return {"ok": True, "before": before, "after": ytdlp_version(), "output": result.stdout[-800:]}
