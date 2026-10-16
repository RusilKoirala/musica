"""Filename sanitizing and the normalized-name duplicate check.

Pendrives are usually FAT32/exFAT and are often plugged into a Windows machine
eventually, so names must survive the strictest filesystem we might meet: no
``? : " / \\ | * < >``, no control characters, no trailing dots or spaces, and no
Windows reserved device names.

The second job here is fuzzy matching: "Arijit Singh - Tum Hi Ho (Official Video)"
and "Tum Hi Ho | Arijit Singh | Full Song" are the same song with different video
IDs, so the video-ID archive will not catch it. We normalize both to the same key
and compare.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

ILLEGAL_CHARS = '<>:"/\\|?*'
MAX_STEM_LENGTH = 150  # leaves room for the extension and a deep playlist folder
MAX_FOLDER_LENGTH = 80  # a playlist folder should still read in a file listing
MAX_FOLDER_BYTES = 120  # the count that actually protects the write

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".wma"}

# Junk that shows up in YouTube titles and says nothing about the recording itself.
_NOISE_PHRASES = (
    r"official\s+(audio\s+)?(video\s+)?(lyrics)?",
    r"lyric+s?(\s+video)?",
    r"audio\s+video",
    r"full\s+(video|song|audio)",
    r"video\s+clip",
    r"\bmv\b",
    r"karaoke",
    r"\bhd\b", r"\b4k\b", r"high\s+quality",
    r"best\s+(bass\s+)?(quality)?",
    r"new\s+song",
    r"download",
    r"hq", r"fq",
    r"official\s*channel",
    r"subs?\s*crib(e|be)\s+for\s+more",
)

# Words carrying no identity: useful for grouping, useless for telling two songs apart.
_STOPWORDS = {"the", "a", "an", "of", "and", "ft", "feat", "featuring", "with", "in", "on", "from"}

_NOISE_RE = re.compile("|".join(_NOISE_PHRASES), re.IGNORECASE)
_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_TRACK_NUMBER_RE = re.compile(r"\d{1,2}")

# yt-dlp rewrites characters Windows forbids into fullwidth look-alikes ("|" -> "｜"),
# and files copied from another tool may already contain them. Treat those as separators
# too, so "Tum Hi Ho｜Live" and "Tum Hi Ho Live" still compare as the same song.
def tokenize(text: str) -> list[str]:
    """Split on anything that is not a letter or a digit, keeping non-Latin scripts."""
    characters = []
    for character in (text or "").lower():
        is_letter = unicodedata.category(character).startswith("L")
        characters.append(character if (is_letter or character.isdigit()) else " ")
    return "".join(characters).split()


def sanitize(name: str, *, fallback: str = "track", max_length: int = MAX_STEM_LENGTH, max_bytes: int = 200) -> str:
    """Return a single path segment that any FAT/NTFS/APFS/ext4 volume will accept."""
    text = (name or "").replace("\u200b", " ").replace("\ufeff", "")
    text = unicodedata.normalize("NFC", text)
    # Drop control characters, then everything the worst-case filesystem forbids.
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = "".join(" " if ch in ILLEGAL_CHARS else ch for ch in text)
    text = re.sub(r"[. ]{2,}", " ", text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip(" .")

    if not text:
        text = fallback
    if len(text) > max_length:
        # Cut on a word boundary so the name still reads well on a car stereo.
        cut = text[:max_length]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        text = cut.strip(" .") or text[:max_length].strip(" .")

    # Filesystems count bytes, not characters. A YouTube "artist" field can be a wall of
    # featured singers, and Devanagari costs three bytes a letter, so 200 characters can
    # blow straight past a 255-byte name and the write fails with ENAMETOOLONG.
    while text and len(text.encode("utf-8", errors="ignore")) > max_bytes:
        text = text[: len(text) - 8].rstrip(" .-_")
        if " " in text and len(text) > 40:
            text = text[: text.rfind(" ")].rstrip(" .-_")

    # "nul.mp3" would be unwritable on Windows; "nul_" is fine everywhere.
    if text.upper().split(".")[0] in _WINDOWS_RESERVED:
        text = "_" + text
    return text or fallback


def sanitize_parts(
    *parts: str,
    fallback: str = "track",
    max_length: int = MAX_FOLDER_LENGTH,
    max_bytes: int = MAX_FOLDER_BYTES,
) -> list[str]:
    """Sanitize a chain of folder/file segments, skipping empties and traversal.

    Folders get the same byte budget as files: a Devanagari playlist title costs
    three bytes a letter, so cutting at 80 characters can still hand back a
    240-byte directory name and the mkdir fails exactly like the file write would.
    """
    out = []
    for part in parts:
        if not part:
            continue
        clean = sanitize(str(part), fallback=fallback, max_length=max_length, max_bytes=max_bytes)
        if clean in (".", ".."):
            continue
        out.append(clean)
    return out


def strip_noise(title: str) -> str:
    """Remove bracketed junk and known noise phrases from a title."""
    text = title or ""
    # Only drop a bracketed group when it looks like marketing noise, never "(2019 Remaster)".
    def _drop_bracket(match: re.Match[str]) -> str:
        inner = match.group(1)
        return "" if _NOISE_RE.search(inner) else match.group(0)

    text = re.sub(r"[(\[{](.*?)[)\]}]", _drop_bracket, text, flags=re.IGNORECASE | re.DOTALL)
    text = _NOISE_RE.sub(" ", text)
    # Titles are usually "Artist - Song | junk"; the pipe/dash tails are noise.
    text = re.split(r"\s[\|｜]\s", text)[0]
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip(" -–—~|·:;,.")


def normalize_key(title: str, artist: str = "") -> str:
    """A comparable identity for a song: lowercased, punctuation-free, noise-free."""
    base = strip_noise(title)
    if artist:
        # Fold the channel/artist in only when it is not already part of the title,
        # so "Arijit Singh - Tum Hi Ho" and "Tum Hi Ho" still collide.
        if not _contains_token(base, artist):
            base = f"{base} {artist}"
    cleaned = tokenize(base)
    # "01 - Artist - Song" numbering (car mode) must not make the same song look new.
    tokens = [t for t in cleaned if t not in _STOPWORDS and not _TRACK_NUMBER_RE.fullmatch(t)]
    return " ".join(sorted(tokens))


def similar(key_a: str, key_b: str) -> float:
    """Similarity in 0..1 between two normalized keys."""
    if not key_a or not key_b:
        return 0.0
    if key_a == key_b:
        return 1.0
    set_a, set_b = set(key_a.split()), set(key_b.split())
    if not set_a or not set_b:
        return 0.0

    overlap = len(set_a & set_b)
    smaller = min(len(set_a), len(set_b))
    jaccard = overlap / len(set_a | set_b)
    ratio = difflib.SequenceMatcher(None, key_a, key_b).ratio()
    score = max(jaccard, ratio)

    # "despacito luis fonsi" vs "despacito" is one song, yet Jaccard calls it 0.5.
    # Full containment therefore outranks the raw token math. A single shared word is
    # weaker evidence ("hello" inside "hello world"), so it scores lower and the caller
    # must confirm it with matching durations.
    if overlap == smaller:
        score = max(score, 0.93 if smaller >= 2 else 0.90)
    return score


def key_granularity(key: str) -> int:
    """How many identity tokens a key carries; 1 means "needs a second opinion"."""
    return len((key or "").split())


def _contains_token(text: str, needle: str) -> bool:
    tokens = set(tokenize(text))
    needle_tokens = set(tokenize(needle))
    return bool(needle_tokens) and needle_tokens <= tokens


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in _AUDIO_EXTENSIONS


def relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name
