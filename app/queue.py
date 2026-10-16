"""The download queue: worker threads in, live progress out.

yt-dlp is blocking, so downloads run on a small thread pool. Progress hooks mutate a
track's state and then publish a snapshot to every Server-Sent-Events subscriber through
``loop.call_soon_threadsafe`` - the only safe way to wake an asyncio queue from a thread.

We publish whole snapshots instead of diffs. The payload is a few kilobytes, and it makes
the browser side impossible to desynchronise after a reconnect or a missed event.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from . import config, library, ytdl
from .sanitize import normalize_key

# Do not flood the browser: at most one progress event per track per interval.
_PROGRESS_INTERVAL = 0.25
MAX_HISTORY = 20

PENDING, RUNNING, DONE, SKIPPED, ERROR, CANCELLED = (
    "pending", "running", "done", "skipped", "error", "cancelled",
)
_SETTLED = {DONE, SKIPPED, ERROR, CANCELLED}


class Queue:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs: list[dict[str, Any]] = []
        self.current_id: str | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._futures: list[Future] = []
        self._pause = threading.Event()  # set == running
        self._pause.set()
        self._cancel = threading.Event()
        self._last_progress: dict[str, float] = {}
        self.paused = False
        self.logs: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ plumbing

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish(self, event: str, payload: Any = None) -> None:
        message = json.dumps({"event": event, "payload": payload, "at": round(time.time(), 3)}, default=str)
        loop = self.loop
        if loop is None or not self.subscribers:
            return

        def deliver() -> None:
            for queue in list(self.subscribers):
                try:
                    queue.put_nowait(message)
                except (asyncio.QueueFull, RuntimeError):
                    # A stalled tab should never stall a download.
                    pass

        try:
            if loop.is_running():
                loop.call_soon_threadsafe(deliver)
            else:
                deliver()
        except RuntimeError:
            pass

    def log(self, level: str, text: str) -> None:
        entry = {"at": round(time.time(), 3), "level": level, "text": text}
        with self.lock:
            self.logs.append(entry)
            self.logs = self.logs[-400:]
        self.publish("log", entry)

    # ------------------------------------------------------------------- state

    def state(self) -> dict[str, Any]:
        with self.lock:
            jobs = [self._job_view(job) for job in self.jobs[-MAX_HISTORY:]]
            active = next((job for job in jobs if job["status"] in ("queued", "running")), None)
            return {
                "paused": self.paused,
                "active_job_id": active["id"] if active else None,
                "jobs": list(reversed(jobs)),
                "counts": self._counts(jobs),
            }

    @staticmethod
    def _counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
        totals = {status: 0 for status in (DONE, SKIPPED, ERROR, PENDING, RUNNING, CANCELLED)}
        for job in jobs:
            for key, value in job["counts"].items():
                totals[key] = totals.get(key, 0) + value
        return totals

    @staticmethod
    def _job_view(job: dict[str, Any]) -> dict[str, Any]:
        tracks = job["tracks"]
        counts = {status: 0 for status in (DONE, SKIPPED, ERROR, PENDING, RUNNING, CANCELLED)}
        for track in tracks:
            counts[track["status"]] = counts.get(track["status"], 0) + 1

        total = len(tracks)
        remaining = counts[PENDING] + counts[RUNNING]
        if job.get("cancelled") and remaining:
            status = "cancelling" if counts[RUNNING] else "cancelled"
        elif counts[RUNNING]:
            status = "running"
        elif remaining:
            status = "queued" if job["started"] else "idle"
        else:
            status = "finished" if total else "empty"

        return {
            "id": job["id"],
            "destination": job["destination"],
            "created_at": job["created_at"],
            "started": job["started"],
            "label": job["label"],
            "status": status,
            "total": total,
            "remaining": remaining,
            "processed": total - remaining,
            "counts": counts,
            "bytes_done": sum(t.get("size") or 0 for t in tracks),
            "tracks": [dict(track) for track in tracks],
        }

    # ---------------------------------------------------------------- enqueue

    def enqueue(
        self,
        items: list[dict[str, Any]],
        destination: str,
        settings: dict[str, Any],
        *,
        label: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        """Add tracks to the queue, pre-filtering the ones we already have."""
        if not items:
            raise ValueError("Nothing was selected to download.")
        library.invalidate(destination)
        archive = library.archive_path(destination, bool(settings.get("archive_on_drive", True)))
        archive_ids = library.read_archive_ids(archive)
        index = library.index_for(destination).build()

        job_id = uuid.uuid4().hex[:12]
        tracks: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        with self.lock:
            already_queued = {
                track["id"].lower()
                for job in self.jobs
                for track in job["tracks"]
                if track["status"] not in _SETTLED and track.get("id")
            }

        for item in items:
            track = {
                "key": uuid.uuid4().hex[:10],
                "id": str(item.get("id") or ""),
                "url": item.get("url") or "",
                "title": item.get("title") or "Untitled",
                "artist": item.get("artist") or "",
                "duration": item.get("duration"),
                "thumbnail": item.get("thumbnail"),
                "playlist": item.get("playlist"),
                "playlist_index": item.get("playlist_index"),
                # Spotify items carry the clean metadata the tagger should trust.
                "source": item.get("source") or "youtube",
                "album": item.get("album") or "",
                "year": item.get("year") or "",
                "uploader": item.get("uploader") or "",
                "status": PENDING,
                "percent": 0.0,
                "speed": None,
                "eta": None,
                "stage": None,
                "error": None,
                "skip_reason": None,
                "path": None,
                "size": None,
                "force": force,
            }

            if not force:
                # The archive only says "downloaded once". If the file was deleted since,
                # the song is not on the drive and must come back.
                if track["id"] and track["id"].lower() in archive_ids and index.find_duplicate(
                    track["title"], track["artist"], track["duration"]
                ):
                    track["status"] = SKIPPED
                    track["skip_reason"] = "already-downloaded"
                    track["stage"] = "In the drive's archive"
                elif track["id"] and track["id"].lower() in already_queued:
                    track["status"] = SKIPPED
                    track["skip_reason"] = "already-queued"
                    track["stage"] = "Already in the queue"
                else:
                    if settings.get("skip_name_duplicates", True):
                        duplicate = index.find_duplicate(track["title"], track["artist"], track["duration"])
                        if duplicate:
                            track["status"] = SKIPPED
                            track["skip_reason"] = "same-song"
                            track["path"] = duplicate["path"]
                            track["stage"] = f"Same song already on the drive ({duplicate['score']:.0%} match)"
                    key = normalize_key(track["title"], track["artist"])
                    if key and key in seen_keys:
                        track["status"] = SKIPPED
                        track["skip_reason"] = "duplicate-in-selection"
                        track["stage"] = "Listed twice in this selection"
                    elif key:
                        seen_keys.add(key)

            tracks.append(track)

        with self.lock:
            job = {
                "id": job_id,
                "destination": destination,
                "settings": dict(settings),
                "label": label or (tracks[0]["playlist"] or tracks[0]["title"] if tracks else "Download"),
                "created_at": time.time(),
                "started": False,
                "cancelled": False,
                "stop_requested": False,
                "tracks": tracks,
            }
            self.jobs.append(job)
            self.jobs = self.jobs[-MAX_HISTORY:]
            self.current_id = job_id

        to_run = [track for track in tracks if track["status"] == PENDING]
        self.log("info", f"Queued {len(to_run)} of {len(tracks)} track(s) to {destination}.")
        self.publish("update", self.state())

        if to_run:
            self._start(settings, job, to_run)
        else:
            self.publish("update", self.state())
        return {"job_id": job_id, "queued": len(to_run), "skipped": len(tracks) - len(to_run)}

    def _start(self, settings: dict[str, Any], job: dict[str, Any], tracks: list[dict[str, Any]]) -> None:
        with self.lock:
            workers = max(1, int(settings.get("concurrency", 1)))
            if self._pool is None or self._pool._max_workers != workers:  # noqa: SLF001
                self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="musica")
            self._pause.set()
            self.paused = False
            self._cancel.clear()
            job["cancelled"] = False
            job["stop_requested"] = False

        for track in tracks:
            future = self._pool.submit(self._run_track, job, track, settings)
            self._futures.append(future)
        self._futures = [f for f in self._futures if not f.done()]

    def _run_track(self, job: dict[str, Any], track: dict[str, Any], settings: dict[str, Any]) -> None:
        if self._cancel.is_set():
            with self.lock:
                track["status"] = CANCELLED
            self.publish("update", self.state())
            return
        if not self._pause.wait(timeout=None):
            return

        with self.lock:
            if self._cancel.is_set():
                track["status"] = CANCELLED
                return
            job["started"] = True
            track["status"] = RUNNING
            track["stage"] = "Connecting"
        self.publish("update", self.state())

        def on_progress(progress: dict[str, Any]) -> None:
            status = progress.get("status")
            with self.lock:
                if status == "downloading":
                    track["stage"] = "Downloading"
                    if progress.get("percent") is not None:
                        track["percent"] = float(progress["percent"])
                    track["speed"] = progress.get("speed")
                    track["eta"] = progress.get("eta")
                elif status == "finished":
                    track["stage"] = "Converting to audio"
                    track["percent"] = 100.0
                    track["speed"] = None
                elif status == "error":
                    track["stage"] = "Failed"
            now = time.time()
            if status not in ("downloading",) or now - self._last_progress.get(track["key"], 0) > _PROGRESS_INTERVAL:
                self._last_progress[track["key"]] = now
                self.publish("update", self.state())

        try:
            result = ytdl.download_item(
                {"url": track["url"], **track},
                job["destination"],
                settings,
                on_progress=on_progress,
                force=bool(track.get("force")),
            )
        except Exception as exc:  # a crash in the worker must not strand the queue
            result = {"status": ERROR, "error": str(exc)}

        with self.lock:
            if result["status"] == "done":
                track["status"] = DONE
                track["path"] = result.get("path")
                track["relative"] = result.get("relative")
                track["size"] = result.get("size")
                track["percent"] = 100.0
                track["stage"] = "Saved"
                track["error"] = None
            else:
                track["status"] = ERROR if not self._cancel.is_set() else CANCELLED
                track["error"] = result.get("error") or "Unknown error"
                track["stage"] = "Failed"
            if not self._cancel.is_set():
                track["force"] = False

        if result["status"] == "done":
            self.log("ok", f"Saved {track['relative'] or track['title']}")
            if track.get("source") == "spotify" and (result.get("tags") or {}).get("cover") != "spotify":
                self.log("warn", f"{track['title']}: the Spotify cover could not be fetched, kept YouTube's picture")
        else:
            self.log("error", f"{track['title']}: {track['error']}")
        self._last_progress.pop(track["key"], None)
        self.publish("update", self.state())

    # --------------------------------------------------------------- controls

    def set_paused(self, paused: bool) -> dict[str, Any]:
        with self.lock:
            self.paused = paused
            if paused:
                self._pause.clear()
            else:
                self._pause.set()
        self.log("info", "Queue paused." if paused else "Queue resumed.")
        self.publish("update", self.state())
        return self.state()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        with self.lock:
            for job in self.jobs:
                if any(track["status"] == PENDING for track in job["tracks"]):
                    job["stop_requested"] = True
                    job["cancelled"] = True
                    for track in job["tracks"]:
                        if track["status"] == PENDING:
                            track["status"] = CANCELLED
                            track["stage"] = "Cancelled"
        self.log("warn", "Cancelling: pending tracks were dropped.")
        self.publish("update", self.state())
        return self.state()

    def retry(self, track_keys: list[str], force: bool = True) -> dict[str, Any]:
        """Re-queue failed or skipped tracks, optionally ignoring both duplicate checks."""
        items, destination = [], None
        with self.lock:
            for job in self.jobs:
                for track in job["tracks"]:
                    if track["key"] in track_keys and (force or track["status"] in (ERROR, CANCELLED)):
                        items.append(dict(track))
                        destination = destination or job["destination"]
                        track["status"] = PENDING
                        track["error"] = None
                        track["skip_reason"] = None
                        track["percent"] = 0.0
                        track["force"] = force
        if not items:
            return {"queued": 0}
        assert destination
        job = {"id": uuid.uuid4().hex[:12], "destination": destination, "created_at": time.time(),
               "started": False, "cancelled": False, "stop_requested": False, "label": "Retry",
               "tracks": items, "settings": config.load()}
        with self.lock:
            self.jobs.append(job)
            self.jobs = self.jobs[-MAX_HISTORY:]
        self.publish("update", self.state())
        self._start(job["settings"], job, items)
        return {"queued": len(items)}

    def clear_finished(self) -> dict[str, Any]:
        with self.lock:
            self.jobs = [
                job for job in self.jobs
                if any(track["status"] not in _SETTLED for track in job["tracks"])
            ]
        self.publish("update", self.state())
        return self.state()


# One process-wide queue: the app is a single-user desktop tool.
queue = Queue()
