"""Settings for running Musica on a server instead of a laptop.

Everything here is driven by environment variables so a Docker container can be
configured without touching the code. With none of them set the app behaves exactly
like the local desktop version.

  MUSICA_PASSWORD      require this password before any /api call (cookie based)
  MUSICA_ROOT          keep every read and write inside this folder (e.g. /music)
  MUSICA_ALLOW_FORMAT  "0" hides and blocks drive formatting
  MUSICA_CORS_ORIGINS  comma separated origins allowed to call the API from elsewhere
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

COOKIE_NAME = "musica_session"


def password() -> str:
    return os.environ.get("MUSICA_PASSWORD", "")


def auth_enabled() -> bool:
    return bool(password())


def session_token() -> str:
    """A stable token derived from the password, so a restart keeps people signed in."""
    secret = password().encode()
    return hmac.new(secret, b"musica-session-v1", hashlib.sha256).hexdigest()


def check_password(candidate: str) -> bool:
    return auth_enabled() and hmac.compare_digest(candidate.encode(), password().encode())


def check_token(token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(token or "", session_token())


def root() -> Path | None:
    value = os.environ.get("MUSICA_ROOT", "").strip()
    return Path(value).expanduser().resolve() if value else None


def allow_format() -> bool:
    return os.environ.get("MUSICA_ALLOW_FORMAT", "1").strip().lower() not in ("0", "false", "no", "off")


def cors_origins() -> list[str]:
    raw = os.environ.get("MUSICA_CORS_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def inside_root(path: str | Path) -> bool:
    base = root()
    if base is None:
        return True
    try:
        Path(path).expanduser().resolve().relative_to(base)
        return True
    except ValueError:
        return False


def clamp(path: str | Path) -> Path:
    """The path itself when it is allowed, otherwise the root folder."""
    target = Path(path).expanduser()
    base = root()
    if base is None or inside_root(target):
        return target
    return base
