"""One real track, start to finish: search -> resolve -> download -> tags on disk.
Runs twice: straight onto the folder, and through the local staging copy path."""

import json
import shutil
import subprocess
from pathlib import Path

from app import config, library, ytdl

BASE = Path(__file__).resolve().parent / ".work"
DEST = BASE / "pendrive"
STAGED = BASE / "staged-target"
for path in (DEST, STAGED):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)

settings = config.load()
settings.update({"destination": str(DEST), "format": "mp3", "quality": "192", "playlist_subfolders": True})

print("resolving ...")
preview = ytdl.resolve("tum hi ho arijit singh", limit=2)
item = preview["items"][0]
print("target:", json.dumps({k: item[k] for k in ("id", "title", "duration")}, ensure_ascii=False))

stages = []
result = ytdl.download_item(item, DEST, settings, on_progress=lambda p: stages.append(p["status"]))
print("progress statuses seen:", sorted(set(stages)))
print("result:", json.dumps({k: v for k, v in result.items() if k != "item"}, ensure_ascii=False)[:300])

if result["status"] != "done":
    raise SystemExit("download failed")

audio = Path(result["path"])
print("\nfiles in destination:")
for path in sorted(DEST.rglob("*")):
    if path.is_file():
        print(f"   {path.relative_to(DEST)}  {path.stat().st_size:,} bytes")

leftovers = [p for p in DEST.rglob("*") if p.suffix.lower() in {".webm", ".m4a", ".jpg", ".webp", ".part"}]
print("leftovers that should have been cleaned:", [str(p.relative_to(DEST)) for p in leftovers])

print("\n--- tag check (what a car stereo would read) ---")
subprocess.run(["ffprobe", "-hide_banner", "-show_format", str(audio)], check=False)

from mutagen.id3 import ID3

tags = ID3(audio)
for frame in ("TIT2", "TPE1", "TPE2", "TALB", "TRCK", "TDRC", "TCON"):
    print(f"{frame}: {tags[frame].text if frame in tags else 'MISSING'}")
pictures = tags.getall("APIC")
print("cover art:", f"{len(pictures)} image, {pictures[0].mime}, {len(pictures[0].data):,} bytes" if pictures else "MISSING")

print("\n--- duplicate rules on the finished drive ---")
print("archive contents:", (DEST / library.ARCHIVE_FILENAME).read_text().strip().splitlines())
print("in archive:", library.in_archive(library.read_archive_ids(library.archive_path(DEST, True)), item["id"]))
index = library.index_for(DEST).build(force=True)
same = index.find_duplicate(item["title"], item["artist"], item["duration"])
print("same title again ->", "SKIP" if same else "would re-download", same)

# ---------------------------------------------------- staging path (slow drive)
print("\n=== staging pass: pretend the destination is a USB stick ===")
original = ytdl.should_stage
ytdl.should_stage = lambda destination, s: True
try:
    staged_result = ytdl.download_item(item, STAGED, settings, force=True)
finally:
    ytdl.should_stage = original
print("staged result:", json.dumps({k: v for k, v in staged_result.items() if k != "item"}, ensure_ascii=False)[:260])

landed = sorted(p.relative_to(STAGED) for p in STAGED.rglob("*") if p.is_file())
print("files on the target folder:", [str(p) for p in landed])
out_dir = ytdl.staging_root() / "out"
remaining = [p for p in out_dir.rglob("*") if p.is_file()] if out_dir.is_dir() else []
print("staging folder left behind:", [p.name for p in remaining])
print("pending archive still there:", (ytdl.staging_root() / ytdl.PENDING_ARCHIVE_NAME).exists())
merged = (STAGED / library.ARCHIVE_FILENAME)
print("merged archive:", merged.read_text().strip().splitlines() if merged.is_file() else "MISSING")
