"""
Login rate-limiting (stored in the server-side session) and session secret management.

Rate limit: max 5 failed attempts within a 5-minute window → HTTP 429.
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request

_MAX_ATTEMPTS = 5
_WINDOW_SEC   = 300   # 5 minutes


# ── session secret ────────────────────────────────────────────────────────────

def session_secret() -> str:
    """
    Return the session signing secret.

    Priority:
      1. SESSION_SECRET environment variable.
      2. A secret persisted in Security/.session_secret  (auto-created on first run).
    """
    from_env = os.environ.get("SESSION_SECRET", "").strip()
    if from_env:
        return from_env

    secret_file = Path(__file__).resolve().parent / ".session_secret"
    if secret_file.is_file():
        return secret_file.read_text().strip()

    secret = secrets.token_hex(32)
    try:
        secret_file.write_text(secret)
    except Exception:
        pass
    return secret


# ── rate limiting ─────────────────────────────────────────────────────────────

def check_login_rate_limit(request: Request) -> None:
    """Raise HTTP 429 if the caller has exceeded the login attempt limit."""
    now         = time.time()
    attempts    = request.session.get("_login_attempts", 0)
    window_start= request.session.get("_login_window_start", 0.0)

    if now - window_start > _WINDOW_SEC:
        # Window expired — reset silently.
        request.session["_login_attempts"]     = 0
        request.session["_login_window_start"] = now
        return

    if attempts >= _MAX_ATTEMPTS:
        remaining = int(_WINDOW_SEC - (now - window_start))
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Please try again in {remaining} seconds.",
        )


def record_login_failure(request: Request) -> None:
    """Increment the failure counter for the current window."""
    now          = time.time()
    window_start = request.session.get("_login_window_start", now)
    attempts     = request.session.get("_login_attempts", 0)

    if now - window_start > _WINDOW_SEC:
        request.session["_login_attempts"]     = 1
        request.session["_login_window_start"] = now
    else:
        request.session["_login_attempts"] = attempts + 1


def clear_login_attempts(request: Request) -> None:
    """Reset the rate-limit counters after a successful login."""
    request.session.pop("_login_attempts",     None)
    request.session.pop("_login_window_start", None)
