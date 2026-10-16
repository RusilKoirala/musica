"""Quick checks on the naming rules, which are the easiest thing to get wrong."""

from app.sanitize import normalize_key, sanitize, similar, strip_noise
from app import drives

NAME_CASES = [
    "Arijit Singh - Tum Hi Ho (Official Video)",
    "Tum Hi Ho | Arijit Singh | Full Song",
    'Gerni Wairo?/:*"<>|  feat. X  ',
    "nul",
    "CON.mp3",
    "   ",
    "a" * 300,
]
for case in NAME_CASES:
    print("sanitize ", repr(case)[:48], "->", repr(sanitize(case)))

pairs = [
    ("Arijit Singh - Tum Hi Ho (Official Video)", "Tum Hi Ho", "Arijit Singh"),
    ("Despacito (Lyric Video)", "Despacito", "Luis Fonsi"),
    ("Tum Hi Ho", "Tum Jao Kahin", "Arijit Singh"),
    ("Shape of You", "Perfect", "Ed Sheeran"),
]
print()
for title_a, title_b, artist in pairs:
    a, b = normalize_key(title_a, ""), normalize_key(title_b, artist)
    verdict = "SAME" if similar(a, b) >= 0.86 else "different"
    print(f"{verdict:9} {similar(a,b):.2f}  {a!r} vs {b!r}")

print()
print("noise  ", repr(strip_noise("Despacito (Lyric Video) - Luis Fonsi ft. Daddy Yankee [Official Audio]")))
print()
info = drives.list_drives()
print("os      ", info["os"], "fallback", info["fallback"])
for drive in info["drives"]:
    print("drive   ", drive["path"], drive["label"], drive["kind"], drive["free_human"], "of", drive["total_human"])
if not info["drives"]:
    print("drive    (no removable drive detected - expected on a laptop with no pendrive)")
