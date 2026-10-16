"""Check the frontend's wiring: no dangling element ids, no missing svg symbols.

Catches the class of bug where app.js reaches for an element that index.html never
declares, which would otherwise only show up as a silent half-working page.
"""

import re
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
html = (STATIC / "index.html").read_text()
js = (STATIC / "app.js").read_text()
css = (STATIC / "styles.css").read_text()

problems = []

# ------------------------------------------------------------------ element ids
declared_ids = set(re.findall(r'id="([\w-]+)"', html))
used_ids = set(re.findall(r"\$\('([\w-]+)'\)", js))
cached = re.search(r"\[\n?\s*((?:\s*'[A-Za-z-]+',)+)\s*\]\.forEach", js)
if cached:
    used_ids |= set(re.findall(r"'([\w-]+)'", cached.group(1)))

missing = sorted(used_ids - declared_ids)
if missing:
    problems.append(f"app.js reaches for ids that index.html does not define: {missing}")

unused = sorted(declared_ids - used_ids)
if unused:
    print(f"note: ids declared but never looked up in js: {unused}")

# ----------------------------------------------------------------------- symbols
symbols = set(re.findall(r'<symbol id="([\w-]+)"', html))
referenced = set(re.findall(r'href="#([\w-]+)"', html)) | set(re.findall(r"icon\('([\w-]+)'\)", js))
referenced |= set(re.findall(r"'#(i-[\w-]+)'", js))
dangling = sorted(referenced - symbols)
if dangling:
    problems.append(f"references to undefined <symbol>: {dangling}")

# ------------------------------------------------------------------ css classes
classes_in_css = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
classes_used = set()
for match in re.finditer(r'class="([^"]*)"', html):
    classes_used |= {c for c in match.group(1).split() if not c.startswith("#")}
for match in re.finditer(r'class="([^"$]*?)"', js):
    classes_used |= {c for c in match.group(1).split() if c and "{" not in c and "$" not in c}
for match in re.finditer(r'classList\.(?:add|remove|toggle)\("([\w-]+)"\)', js):
    classes_used.add(match.group(1))
for match in re.finditer(r'`\.([\w-]+)`', js):
    classes_used.add(match.group(1))

styless = {
    "track", "is-owned", "is-running", "is-error", "is-done", "is-pending", "is-skipped",
    "is-cancelled", "cover", "none", "badge", "owned", "done", "error", "note", "ok", "bad",
    "bar", "convert", "job-status", "job-head", "rate", "live", "dead", "busy", "toast",
    "fineprint",
}
unstyled = sorted(c for c in classes_used - classes_in_css if c not in {"sprite"} and not c.startswith("i-"))
if unstyled:
    problems.append(f"classes used in markup with no rule in styles.css: {unstyled}")

# --------------------------------------------------------------------- js hygiene
for name in ("refreshOwnership", "renderQueue", "renderPreview", "renderDestination", "openBrowser"):
    if f"function {name}" not in js:
        problems.append(f"{name}() is called but never defined")

if "state.settings?.destination" in js and "state.settings" not in js:
    problems.append("settings is used before it is loaded")

print(f"{len(declared_ids)} ids in html · {len(used_ids)} looked up · {len(symbols)} symbols · {len(classes_in_css)} css classes")
if problems:
    print("\n".join(f"FAIL  {p}" for p in problems))
    sys.exit(1)
print("PASS  frontend wiring is consistent")
