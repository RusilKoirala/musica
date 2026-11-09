

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config, drives, hosting, library, spotify, ytdl
from .queue import queue

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR.parent / "static"
# The Next.js build (`npm run build` in web/). When it is missing the old page is served.
WEB_DIR = Path(os.environ.get("MUSICA_WEB_DIR") or (APP_DIR.parent / "web" / "out"))
AUDIO_EXTENSIONS = {"." + ext for ext in config.CODEC_EXTENSIONS.values()}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    queue.bind_loop(asyncio.get_running_loop())
    ffmpeg = ytdl.ffmpeg_available()
    queue.log(
        "info",
        f"Musica {__version__} ready. yt-dlp {ytdl.ytdlp_version()}, ffmpeg {ffmpeg['version'] or 'MISSING'}.",
    )
    if not ffmpeg["ok"]:
        queue.log("error", "ffmpeg was not found on PATH. Install it or MP3 conversion will fail.")
    if not ytdl.js_runtime():
        queue.log("warn", "No JavaScript runtime (deno, node or bun) on PATH, so YouTube may refuse lookups.")
    stale = ytdl.purge_staging()
    if stale:
        queue.log("info", f"Cleared {stale} staged file(s) from an interrupted run.")
    yield


app = FastAPI(title="Musica", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)

if hosting.cors_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=hosting.cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Reachable without signing in: the page needs these to know whether to show a login.
_OPEN_PATHS = {"/api/health", "/api/login", "/api/logout"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if (
        hosting.auth_enabled()
        and path.startswith("/api/")
        and path not in _OPEN_PATHS
        and request.method != "OPTIONS"
        and not hosting.check_token(request.cookies.get(hosting.COOKIE_NAME))
    ):
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    return await call_next(request)


# ----------------------------------------------------------------------- models

class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=3000)
    limit: int | None = None
    prefer_playlist: bool | None = None


class DownloadRequest(BaseModel):
    destination: str = Field(min_length=1)
    items: list[dict[str, Any]] = Field(default_factory=list)
    label: str = ""
    force: bool = False
    # Per-batch overrides so the preview bar can say "this run only, as 192k m4a".
    settings: dict[str, Any] = Field(default_factory=dict)


class SettingsRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class QueueControlRequest(BaseModel):
    paused: bool


class RetryRequest(BaseModel):
    keys: list[str] = Field(default_factory=list)
    force: bool = True


class BrowseRequest(BaseModel):
    path: str | None = None
    include_files: bool = False


class LoginRequest(BaseModel):
    password: str = ""


class FormatRequest(BaseModel):
    path: str = Field(min_length=1)
    fstype: str = "fat32"
    label: str = ""
    # The UI makes the person type the word FORMAT; nothing here trusts a stray click.
    confirm: str = ""


# ---------------------------------------------------------------------- errors

@app.exception_handler(ValueError)
async def _value_error(_request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ----------------------------------------------------------------------- health

@app.get("/api/health")
def health() -> dict[str, Any]:
    ffmpeg = ytdl.ffmpeg_available()
    return {
        "app": "musica",
        "version": __version__,
        "os": drives.list_drives()["os"],
        "ytdlp": ytdl.ytdlp_version(),
        "ffmpeg": ffmpeg,
        "js_runtime": ytdl.js_runtime(),
        "python": sys.version.split()[0],
        "auth": hosting.auth_enabled(),
        "can_format": hosting.allow_format(),
        "root": str(hosting.root()) if hosting.root() else None,
    }


@app.get("/api/me")
def me() -> dict[str, Any]:
    # The middleware already rejected anyone without a session.
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginRequest, request: Request) -> JSONResponse:
    if not hosting.auth_enabled():
        return JSONResponse({"ok": True})
    if not hosting.check_password(body.password):
        time.sleep(0.6)  # makes guessing slow
        raise HTTPException(401, "Wrong password.")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        hosting.COOKIE_NAME,
        hosting.session_token(),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(hosting.COOKIE_NAME)
    return response


@app.post("/api/selfupdate")
def selfupdate() -> dict[str, Any]:
    """YouTube changes often, so let the user pull a fresh yt-dlp without a terminal."""
    result = ytdl.self_update()
    queue.log("ok" if result["ok"] else "error",
              f"yt-dlp {result['before']} -> {result['after']}" if result["ok"]
              else f"yt-dlp update failed: {result.get('error')}")
    queue.publish("update", queue.state())
    return result


# --------------------------------------------------------------------- settings

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    settings = config.load()
    return {
        "settings": settings,
        "codecs": sorted(config.CODEC_EXTENSIONS, key=lambda c: ["mp3", "m4a", "opus", "flac", "wav", "ogg"].index(c)),
        "bitrates": list(config.MP3_BITRATES),
    }


@app.put("/api/settings")
def put_settings(body: SettingsRequest) -> dict[str, Any]:
    if "destination" in body.settings:
        # Validate early rather than failing mid-download on a slow drive.
        destination = str(body.settings["destination"]).strip()
        if destination:
            try:
                drives.ensure_writable(destination)
            except (OSError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from exc
    try:
        return config.update(body.settings)
    except OSError as exc:
        raise HTTPException(409, f"Settings could not be saved to disk: {exc}") from exc


# ------------------------------------------------------------------- destinations

@app.get("/api/drives")
def get_drives() -> dict[str, Any]:
    return drives.list_drives()


@app.post("/api/drives/plan")
def format_plan(body: FormatRequest) -> dict[str, Any]:
    """What would be run, so the dialog can show it before anything is erased."""
    if not hosting.allow_format():
        raise HTTPException(403, "Formatting drives is turned off on this server.")
    drive = next((d for d in drives.list_drives()["drives"] if str(d["path"]).rstrip("/") == body.path.rstrip("/")), None)
    if drive is None:
        raise HTTPException(400, "Only a detected removable or external drive can be formatted.")
    if body.label:
        drive = {**drive, "label": body.label}
    try:
        command, warning = drives.format_plan(drive, body.fstype)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"command": " ".join(command), "warning": warning, "confirm": drives.CONFIRM_TOKEN}


@app.post("/api/drives/format")
def format_drive(body: FormatRequest) -> dict[str, Any]:
    """Erase and re-format the stick to something the car will mount."""
    try:
        result = drives.format_drive(body.path, body.fstype, body.confirm, body.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(409, result.get("error") or "The format failed; nothing was changed.")
    library.invalidate(body.path)
    queue.log("ok", f"Formatted {body.path} as {result['fstype']} for the car.")
    return {**result, "drives": drives.list_drives()}


@app.post("/api/folders")
def browse(body: BrowseRequest) -> dict[str, Any]:
    return drives.browse(body.path, body.include_files)


# ---------------------------------------------------------------------- resolve

@app.post("/api/resolve")
def resolve(body: ResolveRequest) -> dict[str, Any]:
    """Metadata only - titles, durations, thumbnails - never the audio."""
    settings = config.load()
    limit = body.limit or int(settings.get("search_results", 10))
    try:
        if spotify.is_spotify(body.query):
            result = spotify.resolve(body.query)
        else:
            result = ytdl.resolve(body.query, limit=limit, prefer_playlist=body.prefer_playlist)
    except ValueError:
        raise
    except Exception as exc:
        if spotify.is_spotify(body.query):
            raise HTTPException(422, f"Spotify did not return that link: {exc}. Check that the playlist is public.") from exc
        detail = str(exc)
        hint = ""
        if "Private video" in detail:
            hint = "That video is private. Add a cookies file in Settings if you own it."
        elif "Sign in to confirm" in detail:
            hint = "YouTube is asking for sign-in. Settings -> cookies.txt (exported from your browser) fixes it."
        elif "not found" in detail.lower() or "HTTP Error 4" in detail:
            hint = "Check the link, or search the song name instead."
        raise HTTPException(422, f"{detail}{(' - ' + hint) if hint else ''}") from exc

    destination = (settings.get("destination") or "").strip() or drives.default_folder()
    archive = library.archive_path(destination, bool(settings.get("archive_on_drive", True)))
    archive_ids = library.read_archive_ids(archive)
    index = library.index_for(destination).build()

    # Flag what we already have while the preview is still on screen, so the user can
    # untick the obvious noise before committing anything.
    existing = index.entries
    for item in result["items"]:
        on_disk = index.find_duplicate(item["title"], item.get("artist", ""), item.get("duration"))
        # An archive line for a file that was since deleted does not make the song "owned".
        item["in_archive"] = library.in_archive(archive_ids, str(item.get("id") or "")) and bool(on_disk)
        duplicate = on_disk if settings.get("skip_name_duplicates", True) else None
        item["name_duplicate"] = duplicate
        if duplicate:
            item["duplicate_display"] = duplicate["display"]
    return {
        **{key: value for key, value in result.items() if key != "items"},
        "destination": destination,
        "count": len(result["items"]),
        "items": result["items"],
        "scanned_files": len(existing),
    }


# ------------------------------------------------------------------------ queue

@app.post("/api/download")
def download(body: DownloadRequest) -> dict[str, Any]:
    settings = config.load()
    settings.update({k: v for k, v in (body.settings or {}).items() if k in config.DEFAULTS})
    try:
        destination = str(drives.ensure_writable(body.destination))
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    items = [item for item in body.items if item.get("url")]
    if not items:
        raise HTTPException(400, "No playable links were selected.")
    if not ytdl.ffmpeg_available()["ok"] and settings.get("format") != "best":
        raise HTTPException(409, "ffmpeg is not installed, so audio conversion will fail. Install it and retry.")

    # Remember a working destination so the next launch starts on the pendrive.
    # Best effort: a read-only or full config directory must not fail the download.
    if settings.get("destination") != destination:
        try:
            config.update({"destination": destination})
        except OSError as exc:
            queue.log("warn", f"Could not save the destination to settings: {exc}")

    try:
        return queue.enqueue(
            items,
            destination,
            settings,
            label=body.label or (items[0].get("playlist") if items else "") or "",
            force=body.force,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/file")
def download_file(path: str) -> FileResponse:
    """Hand a finished song to the browser, for when the server is not your own laptop."""
    target = Path(path).expanduser()
    try:
        target = target.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(404, "That file is gone.") from exc
    if not target.is_file() or target.suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(404, "Only downloaded songs can be fetched.")
    if not hosting.inside_root(target):
        raise HTTPException(403, "That file is outside the music folder.")
    if hosting.root() is None and not any(
        target.is_relative_to(Path(job["destination"]).expanduser().resolve())
        for job in queue.state()["jobs"]
    ):
        # Without a root folder, only serve songs this queue itself wrote.
        raise HTTPException(403, "Only songs from the queue can be fetched.")
    return FileResponse(target, filename=target.name)


@app.get("/api/queue")
def queue_state() -> dict[str, Any]:
    return queue.state()


@app.post("/api/queue/pause")
def queue_pause(body: QueueControlRequest) -> dict[str, Any]:
    return queue.set_paused(body.paused)


@app.post("/api/queue/cancel")
def queue_cancel() -> dict[str, Any]:
    return queue.cancel()


@app.post("/api/queue/retry")
def queue_retry(body: RetryRequest) -> dict[str, Any]:
    if not body.keys:
        raise HTTPException(400, "No tracks were selected for retry.")
    return queue.retry(body.keys, force=body.force)


@app.post("/api/queue/clear")
def queue_clear() -> dict[str, Any]:
    return queue.clear_finished()


@app.get("/api/events")
async def events() -> StreamingResponse:
    """Server-Sent Events: one connection, whole-queue snapshots as they change."""

    async def stream() -> AsyncIterator[bytes]:
        subscriber: asyncio.Queue = queue.subscribe()
        try:
            welcome = json.dumps({"event": "hello", "payload": queue.state(), "at": time.time()}, default=str)
            yield f"event: snapshot\ndata: {welcome}\n\n".encode()
            while True:
                try:
                    message = await asyncio.wait_for(subscriber.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"  # stops proxies from closing an idle stream
                    continue
                yield f"event: snapshot\ndata: {message}\n\n".encode()
        except asyncio.CancelledError:
            raise
        finally:
            queue.unsubscribe(subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, must-revalidate", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )

