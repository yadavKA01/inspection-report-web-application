"""
Utility functions: password hashing, JWT, temp-password generation,
password strength validation, and mock email delivery.
"""
from __future__ import annotations

import os
import random
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

# ---------------------------------------------------------------------------
# JWT configuration — override SECRET via environment in production
# ---------------------------------------------------------------------------
JWT_SECRET_KEY: str = os.environ.get(
    "JWT_SECRET_KEY",
    "CHANGE_ME_use_a_long_random_secret_key_in_production_abc123xyz987",
)
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_HOURS: int = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))


# ---------------------------------------------------------------------------
# Password hashing (bcrypt, cost factor 12)
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    """Return a bcrypt hash string for plain."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt comparison."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Temporary password generator
# ---------------------------------------------------------------------------
_LOWER = string.ascii_lowercase
_UPPER = string.ascii_uppercase
_DIGITS = string.digits
_SPECIAL = "!@#$%^&*"
_ALL = _LOWER + _UPPER + _DIGITS + _SPECIAL


def generate_temp_password(length: int = 12) -> str:
    """
    Generate a cryptographically random password that meets complexity rules:
      - at least 1 uppercase, 1 lowercase, 1 digit, 1 special character.
    """
    while True:
        pwd = "".join(secrets.choice(_ALL) for _ in range(length))
        if (
            any(c in _UPPER for c in pwd)
            and any(c in _LOWER for c in pwd)
            and any(c in _DIGITS for c in pwd)
            and any(c in _SPECIAL for c in pwd)
        ):
            return pwd


# ---------------------------------------------------------------------------
# Password strength validation
# ---------------------------------------------------------------------------
def validate_password_strength(password: str) -> Optional[str]:
    """
    Return an error message string if the password is too weak, else None.
    Rules: ≥ 8 chars, 1 uppercase, 1 lowercase, 1 digit, 1 special char.
    """
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one digit."
    if not any(c in _SPECIAL for c in password):
        return f"Password must contain at least one special character ({_SPECIAL})."
    return None


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def create_access_token(
    user_id: str,
    email: str,
    role: str,
    tenant_id: Optional[str],
) -> str:
    """
    Encode a signed JWT containing user identity and tenant context.

    Payload:
      sub       — email (standard JWT subject)
      user_id   — user UUID as string
      role      — 'super_admin' or 'engineer'
      tenant_id — organization slug (None for super_admin)
      exp       — expiry timestamp
    """
    expire = datetime.now(tz=timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": email,
        "user_id": str(user_id),
        "role": role,
        "tenant_id": tenant_id,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode and verify a JWT.  Returns the payload dict or None if invalid / expired.
    """
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Email delivery via Resend
# ---------------------------------------------------------------------------
def send_temp_password_email(
    to_email: str,
    name: str,
    temp_password: str,
    app_url: str = "",
) -> bool:
    """
    Send a temporary password email via Resend.
    Falls back to console output if RESEND_API_KEY is not set.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")

    if not api_key:
        print(
            f"\n[EMAIL — CONSOLE FALLBACK]\n"
            f"  To      : {to_email}\n"
            f"  Subject : Password Reset - SmorX.ai\n"
            f"  Temp pw : {temp_password}\n"
            f"  (Set RESEND_API_KEY to enable real email delivery)\n"
        )
        return True

    try:
        import resend as resend_sdk
        resend_sdk.api_key = api_key
        from_email = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
        resend_sdk.Emails.send({
            "from": f"SmorX.ai <{from_email}>",
            "to": [to_email],
            "subject": "Password Reset - SmorX.ai",
            "html": (
                f"<h3>Password Reset</h3>"
                f"<p>Hi {name},</p>"
                f"<p>Your temporary password is:</p>"
                f"<h2 style='letter-spacing:2px'>{temp_password}</h2>"
                f"<p>Please log in and change it immediately.</p>"
                f"<p><a href='{app_url or 'http://localhost:3000'}/login'>Log in now</a></p>"
            ),
        })
        return True
    except Exception as exc:
        print(f"[EMAIL ERROR] Resend delivery failed: {exc}")
        return False
