"""Car-stereo checks: does the finished MP3 carry what a head unit actually reads?

Grounded in what owner's manuals and real reports say: the unit shows the file name when
song information is missing, plenty of players still cannot parse ID3v2.4, some read only
ID3v1, oversized artwork simply does not render, and a stick formatted NTFS is invisible.
"""

import shutil
import sys
from pathlib import Path

from app import config, ytdl
from app.sanitize import normalize_key

ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = Path(__file__).resolve().parent / ".work"
WORK = WORK_ROOT / "car"

sample = next(
    (
        found
        for found in [
            *sorted(WORK_ROOT.glob("pendrive/*.mp3")),
            *sorted(WORK_ROOT.glob("staged-target/*.mp3")),
        ]
    ),
    None,
)
if sample is None:
    print("SKIP  no sample mp3 under tests/.work; run tests.check_download first")
    sys.exit(2)

if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)
track = WORK / "03 - Arijit Singh - Tum Hi Ho.mp3"
shutil.copy2(sample, track)

settings = dict(config.DEFAULTS)
assert settings["car_mode"] is True, "car mode should default to on for a car library"

item = {
    "id": "Umqb9KENgmk",
    "title": "Tum Hi Ho - Aashiqui 2 | Full Song | Lyrics | Official Video",
    "artist": "Arijit Singh",
    "uploader": "T-Series",
    "playlist": "Late Night Drive",
    "playlist_index": 3,
}
info = {"title": item["title"], "uploader": "T-Series", "release_date": "20130815"}

written = ytdl.write_tags(track, info, item, settings)

problems = []

# ------------------------------------------------------------------------ naming
stem = ytdl.output_stem(item, settings)
if not stem.startswith("03 - "):
    problems.append(f"car mode should number the file name, got {stem!r}")
for junk in ("official", "full song", "lyrics"):
    if junk in stem.lower():
        problems.append(f"file name still carries marketing junk ({junk}): {stem!r}")

# ------------------------------------------------------------------------- frames
from mutagen.id3 import ID3


def raw_frames(path: Path) -> list[str]:
    """Frame IDs as they are really on disk.

    mutagen translates a v2.3 TYER into TDRC when it *loads*, so `ID3(path)` cannot tell a
    correct 2.3 file from a broken one. The bytes have to be read directly.
    """
    data = path.read_bytes()
    if data[:3] != b"ID3":
        return []
    version, flags = data[3], data[5]
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    ids = []
    cursor = 10
    end = 10 + size
    if version >= 3 and flags & 0x40:  # extended header
        if version == 4:
            ext = (data[10] << 21) | (data[11] << 14) | (data[12] << 7) | data[13]
        else:
            ext = int.from_bytes(data[10:14], "big")
        cursor += ext + (4 if version == 3 else 0)
    while cursor + 10 <= end:
        frame_id = data[cursor:cursor + 4].decode("latin-1")
        if not frame_id.strip("\x00"):
            break
        if version >= 3:
            length = int.from_bytes(data[cursor + 4:cursor + 8], "big")
        else:  # ID3v2.2: three-byte size
            length = int.from_bytes(data[cursor + 4:cursor + 7], "big")
            frame_id = frame_id[:3]
        ids.append(frame_id)
        cursor += (10 if version >= 3 else 6) + length
    return ids


tags = ID3(str(track))
on_disk = raw_frames(track)
print("frames on disk:", " ".join(on_disk))
if tags.version[:2] != (2, 3):
    problems.append(f"head units mostly read ID3v2.3, file is {tags.version}")

for frame in ("TIT2", "TPE1", "TPE2", "TALB", "TRCK", "TYER", "TCON"):
    if frame not in on_disk:
        problems.append(f"missing {frame} on disk")
if "TDRC" in on_disk:
    problems.append("TDRC (a 2.4 frame) left inside a 2.3 tag - cars ignore it")

title = str(tags.get("TIT2", ""))
if "Official" in title or "|" in title:
    problems.append(f"TIT2 is raw YouTube noise: {title!r}")
if str(tags.get("TPE1", "")) != "Arijit Singh":
    problems.append(f"TPE1 should be the artist, got {tags.get('TPE1')!r}")
if str(tags.get("TALB", "")) != "Late Night Drive":
    problems.append(f"TALB should group by playlist, got {tags.get('TALB')!r}")
year = tags.get("TYER") or tags.get("TDRC")
if str(year) != "2013":
    problems.append(f"TYER should be the year, got {year!r} (disk: {' '.join(on_disk)})")

# ID3v1.1 tail for readers that go no further than the last 128 bytes
raw = track.read_bytes()
if raw[-128:-125] != b"TAG":
    problems.append("no ID3v1 tail - some head units read only that")

# ------------------------------------------------------------------------- artwork
picture = ytdl._cover_frame(tags)[1]
if picture is None:
    problems.append("no embedded picture at all")
else:
    size = len(picture.data)
    if size > ytdl.CAR_ART_MAX_BYTES:
        problems.append(f"cover art is {size} bytes, above the {ytdl.CAR_ART_MAX_BYTES} car cap")
    if picture.mime != "image/jpeg":
        problems.append(f"cover art mime should be jpeg, got {picture.mime}")
    if int(picture.type) != 3:
        problems.append(f"picture type should be 3 (front cover), got {picture.type}")
    header = picture.data[:3]
    if header[:2] != b"\xff\xd8":
        problems.append(f"picture data is not jpeg: {header!r}")
    dims = ytdl._jpeg_size(picture.data)
    if dims and max(dims) > ytdl.CAR_ART_MAX_EDGE:
        problems.append(f"art is still {dims[0]}x{dims[1]}, above the {ytdl.CAR_ART_MAX_EDGE}px car cap")
    print(f"  art {dims} · {size} bytes")

sidecar = WORK / "folder.jpg"
if not sidecar.exists():
    problems.append("car mode should leave a folder.jpg for units that ignore ID3 pictures")

# ------------------------------------------------------------------ duplicate keys
plain = normalize_key("Tum Hi Ho", "Arijit Singh")
numbered = normalize_key("03 - Arijit Singh - Tum Hi Ho (Official Video)", "")
if plain != numbered:
    problems.append(f"track numbering broke the duplicate key: {plain!r} vs {numbered!r}")

print("frames written:", ", ".join(f"{k}={v}" for k, v in sorted(written.items()) if not k.startswith("art")))

if problems:
    print("\n".join(f"FAIL  {p}" for p in problems))
    sys.exit(1)
print("PASS  the file is tagged, named and sized for a car player")
