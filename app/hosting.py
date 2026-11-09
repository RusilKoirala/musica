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
    secret = password().encode()
    return hmac.new(secret, b"musica-session-v1", hashlib.sha256).hexdigest()


def check_password(candidate: str) -> bool:
    return auth_enabled() and hmac.compare_digest(candidate.encode(), password().encode())


def check_token(token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(token or "", session_token())


def demo_enabled() -> bool:
    flag = os.environ.get("MUSICA_DEMO", "").strip().lower() in ("1", "true", "yes", "on")
    return auth_enabled() and flag


def demo_limit() -> int:
    try:
        return max(1, int(os.environ.get("MUSICA_DEMO_LIMIT", "5")))
    except ValueError:
        return 5


def demo_token() -> str:
    return hmac.new(password().encode(), b"musica-demo-v1", hashlib.sha256).hexdigest()


def role_for(token: str | None) -> str | None:
    if not auth_enabled():
        return "admin"
    if not token:
        return None
    if hmac.compare_digest(token, session_token()):
        return "admin"
    if demo_enabled() and hmac.compare_digest(token, demo_token()):
        return "demo"
    return None


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
    target = Path(path).expanduser()
    base = root()
    if base is None or inside_root(target):
        return target
    return base
