"""Regression: metadata so long it used to kill the write with ENAMETOOLONG.

The real-world shape that broke it was a YouTube 'artist' field that is a wall of
featured singers, a Devanagari title (three utf-8 bytes per letter) and a playlist
name to match. Character counts lie about the size of a filename, so the cap has to
be counted in bytes.

The media comes from this app's own /static mount rather than YouTube: the naming,
conversion and staging path are what is under test, not a site that rate-limits the
machine running the test. Requires the dev server on 127.0.0.1:8137.
"""

import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from app import ytdl
from app.sanitize import MAX_FOLDER_BYTES

BASE = os.environ.get("MUSICA_TEST_BASE", "http://127.0.0.1:8137")
ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = Path(__file__).resolve().parent / ".work"
DEST = WORK_ROOT / "http-longname"
SAMPLE = ROOT / "static" / "dev-sample.mp3"

ARTIST = (
    "Gajendra Pratap Singh, Nikhar Juneja, Ravindra Pratap Singh, Kumar Vishal "
    "Anand, Mamta Sharma, Shreekumar Varma, Devang Dholakia, Neeti Mohan"
)
TITLE = "तुम ही हो (प्यार में मुझको) - Official Full Song"
PLAYLIST = "भोजपुरी सुपरहिट गाना संग्रह, ढेर सारे हिट गाने एक साथ में"


def post(path, payload):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return json.loads(response.read())


def find_job(state, job_id):
    for job in state["jobs"]:
        if job["id"] == job_id:
            return job
    return None


# ------------------------------------------------------------------- sample file
source = next(iter(sorted(WORK_ROOT.glob("pendrive/*.mp3"))), None)
if source is None:
    print("SKIP  no sample mp3 in tests/.work/pendrive; run tests.check_download first")
    sys.exit(2)
shutil.copy2(source, SAMPLE)

if DEST.exists():
    shutil.rmtree(DEST)
DEST.mkdir(parents=True)

try:
    result = post(
        "/api/download",
        {
            "destination": str(DEST),
            "label": PLAYLIST,
            "items": [
                {
                    "id": "longname-regression",
                    "url": f"{BASE}/static/dev-sample.mp3",
                    "title": TITLE,
                    "artist": ARTIST,
                    "duration": 210,
                    "playlist": PLAYLIST,
                }
            ],
            "settings": {
                "stage_locally": True,
                "playlist_subfolders": True,
                "format": "mp3",
                "quality": "320",
                "archive_on_drive": True,
                "skip_name_duplicates": False,
            },
        },
    )
    print("enqueue reply:", json.dumps(result, ensure_ascii=False)[:220])
    job_id = result["job_id"]

    deadline = time.time() + 180
    track = None
    while time.time() < deadline:
        job = find_job(get("/api/queue"), job_id)
        if job and job["tracks"]:
            track = job["tracks"][0]
            if track["status"] in {"done", "error", "skipped", "cancelled"}:
                break
        time.sleep(1)
finally:
    SAMPLE.unlink(missing_ok=True)

if not track:
    print("FAIL  the job never appeared in the queue")
    sys.exit(1)
print(
    "result:",
    json.dumps(
        {k: track.get(k) for k in ("status", "stage", "error", "relative", "size", "tags")},
        ensure_ascii=False,
        indent=2,
    ),
)

if track["status"] != "done":
    print("FAIL  download did not finish cleanly")
    sys.exit(1)

problems = []
files = sorted(p for p in DEST.rglob("*") if p.is_file())
if not files:
    problems.append("nothing landed on the destination")
for path in files:
    stem_bytes = len(path.stem.encode("utf-8"))
    name_bytes = len(path.name.encode("utf-8"))
    print(f"  {name_bytes:>4} bytes ({stem_bytes} stem)  {path.relative_to(DEST)}")
    if stem_bytes > 200:
        problems.append("stem exceeds the byte cap")
    if path.parent != DEST and len(path.parent.name.encode("utf-8")) > MAX_FOLDER_BYTES:
        problems.append(f"folder name too long: {path.parent.name!r}")
    if path.suffix == ".mp3":
        for junk in ("<", ">", '"', "|", "?", "*", "＂", "｜", "⧸"):
            if junk in path.name:
                problems.append(f"illegal/leftover character {junk!r} in {path.name!r}")
    elif path.name != ytdl.library.ARCHIVE_FILENAME:
        problems.append(f"unexpected artefact {path.name!r}")

if not (DEST / ytdl.library.ARCHIVE_FILENAME).exists():
    problems.append("archive was not written to the destination")

staged = [p for p in ytdl.staging_root().rglob("*") if p.is_file()]
if staged:
    problems.append(f"staging still holds {len(staged)} file(s) after promotion")

if problems:
    print("\n".join(f"FAIL  {p}" for p in problems))
    sys.exit(1)
print(f"PASS  pathological metadata produced {len(files)} legal, writable filename(s)")
