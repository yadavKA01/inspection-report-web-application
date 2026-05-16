"""
Password hashing, email validation, temp-password generation, and trial logic.
"""
from __future__ import annotations

import os
import re
import secrets
import string
import time
from typing import Optional

import bcrypt

# ── password hashing ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed* bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def generate_temp_password(length: int = 12) -> str:
    """
    Generate a cryptographically-random temporary password that satisfies
    common complexity rules: upper, lower, digit, special character.
    """
    upper   = string.ascii_uppercase
    lower   = string.ascii_lowercase
    digits  = string.digits
    special = "!@#$%^&*"
    pool    = upper + lower + digits + special

    while True:
        pwd = "".join(secrets.choice(pool) for _ in range(length))
        if (
            any(c in upper   for c in pwd)
            and any(c in lower   for c in pwd)
            and any(c in digits  for c in pwd)
            and any(c in special for c in pwd)
        ):
            return pwd


def validate_password_strength(password: str) -> Optional[str]:
    """
    Return an error message if the password is too weak, or None if it is fine.
    Rules: ≥8 chars, at least one upper, lower, digit, and special character.
    """
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one number"
    if not any(c in string.punctuation for c in password):
        return "Password must contain at least one special character (!@#$%…)"
    return None


# ── email validation ──────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def is_gmail(email: str) -> bool:
    """
    Validate the email address.

    If the environment variable REQUIRE_GMAIL=1 is set (or true/yes) the email
    must end with @gmail.com.  Otherwise any syntactically-valid address passes.
    """
    if not is_valid_email(email):
        return False
    require_gmail = os.environ.get("REQUIRE_GMAIL", "1").strip().lower() in ("1", "true", "yes")
    if require_gmail:
        return email.strip().lower().endswith("@gmail.com")
    return True


def is_admin_email(email: str) -> bool:
    """Return True if *email* is listed in BALLOON_ADMIN_EMAILS (comma-separated env var)."""
    admins = os.environ.get("BALLOON_ADMIN_EMAILS", "").strip().lower()
    if not admins:
        return False
    return email.strip().lower() in {a.strip() for a in admins.split(",")}


# ── trial management ──────────────────────────────────────────────────────────

TRIAL_DAYS = 3


def trial_expired(user: dict) -> bool:
    ts = user.get("trial_started_at")
    if ts is None:
        return False
    return (time.time() - float(ts)) > TRIAL_DAYS * 86400


def trial_remaining_sec(user: dict) -> Optional[float]:
    ts = user.get("trial_started_at")
    if ts is None:
        return None
    remaining = TRIAL_DAYS * 86400 - (time.time() - float(ts))
    return max(0.0, remaining)
