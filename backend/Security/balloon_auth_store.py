"""
MongoDB CRUD for the balloon authentication system.

Collection schema (ai_engine.access_control):
  {
    email:                   str   (unique index, lowercase),
    password_hash:           str   (bcrypt),
    role:                    str   ("user" | "admin"),
    firstname:               str,
    lastname:                str,
    paid:                    bool,
    trial_started_at:        float | None,
    is_temp_password:        bool,
    temp_password_expires_at: float | None,
    created_at:              float,
  }

File backend (no MongoDB):
  set BALLOON_USE_FILE_AUTH=1
  or set BALLOON_AUTH_FILE=C:\\path\\balloon_auth_users.json
"""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path
from typing import Optional

import config
import mongodb as db
from pymongo.errors import DuplicateKeyError

from Security.balloon_auth_file_store import (
    file_create_user as _file_create_user,
    file_get_user as _file_get_user,
    file_init as _file_init,
    file_list_users as _file_list_users,
    file_set_paid as _file_set_paid,
    file_update_password as _file_update_password,
    file_update_trial_start as _file_update_trial_start,
)


def auth_file_path() -> Optional[Path]:
    """If set, all auth reads/writes use this JSON file instead of MongoDB."""
    custom = os.environ.get("BALLOON_AUTH_FILE", "").strip()
    if custom:
        return Path(custom).expanduser()
    if os.environ.get("BALLOON_USE_FILE_AUTH", "").strip().lower() in ("1", "true", "yes"):
        return Path(__file__).resolve().parent.parent / ".Temp" / "balloon_auth_users.json"
    return None


def auth_backend_available() -> bool:
    """True if we can persist users: file auth enabled, or MongoDB ping OK."""
    if auth_file_path():
        return True
    try:
        return db.ping()
    except Exception:
        return False


def _col():
    sec = config.GetConfiguration("SECURITY") or {}
    db_name = sec.get("DATABASE_NAME", "ai_engine")
    col_name = sec.get("COLLECTION_NAME", "access_control")
    return db.GetCollection(db_name, col_name)


# ── initialisation ────────────────────────────────────────────────────────────

def init_db() -> None:
    """Ensure index (Mongo) or empty file (file auth)."""
    fp = auth_file_path()
    if fp:
        _file_init(fp)
        print(f"[auth_store] Balloon auth: FILE backend → {fp.resolve()}")
        return
    try:
        _col().create_index("email", unique=True)
    except Exception as exc:
        print(f"[auth_store] init_db warning: {exc}")


# ── read ──────────────────────────────────────────────────────────────────────

def get_user(email: str) -> Optional[dict]:
    """Return the user document for *email*, or None if not found."""
    fp = auth_file_path()
    if fp:
        return _file_get_user(fp, email)
    try:
        return _col().find_one({"email": email.strip().lower()}, {"_id": 0})
    except Exception as exc:
        print(f"[auth_store] get_user failed for {email.strip().lower()!r}: {exc}")
        traceback.print_exc()
        return None


def list_users() -> list:
    fp = auth_file_path()
    if fp:
        return _file_list_users(fp)
    try:
        return list(_col().find({}, {"_id": 0, "password_hash": 0}))
    except Exception:
        return []


# ── write ─────────────────────────────────────────────────────────────────────

def create_user(
    email: str,
    password_hash: str,
    *,
    role: str = "user",
    firstname: str = "",
    lastname: str = "",
    is_temp_password: bool = False,
    temp_password_expires_at: Optional[float] = None,
) -> bool:
    """
    Insert a new user document.  Returns True on success, False on duplicate
    or any other DB error.
    """
    fp = auth_file_path()
    if fp:
        return _file_create_user(
            fp,
            email,
            password_hash,
            role=role,
            firstname=firstname,
            lastname=lastname,
            is_temp_password=is_temp_password,
            temp_password_expires_at=temp_password_expires_at,
        )
    try:
        _col().insert_one(
            {
                "email":                    email.strip().lower(),
                "password_hash":            password_hash,
                "role":                     role,
                "firstname":                firstname.strip(),
                "lastname":                 lastname.strip(),
                "paid":                     False,
                "trial_started_at":         None,
                "is_temp_password":         is_temp_password,
                "temp_password_expires_at": temp_password_expires_at,
                "created_at":               time.time(),
            }
        )
        return True
    except DuplicateKeyError as exc:
        print(f"[auth_store] create_user duplicate email: {exc}")
        return False
    except Exception as exc:
        print(f"[auth_store] create_user error: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False


def update_password(email: str, new_hash: str, is_temp_password: bool = False) -> bool:
    """Replace the stored password hash and clear the temporary-password flag."""
    fp = auth_file_path()
    if fp:
        return _file_update_password(fp, email, new_hash, is_temp_password=is_temp_password)
    try:
        result = _col().update_one(
            {"email": email.strip().lower()},
            {
                "$set": {
                    "password_hash":            new_hash,
                    "is_temp_password":         is_temp_password,
                    "temp_password_expires_at": None,
                }
            },
        )
        return result.modified_count > 0
    except Exception:
        return False


def update_trial_start(email: str, ts: float) -> bool:
    fp = auth_file_path()
    if fp:
        return _file_update_trial_start(fp, email, ts)
    try:
        result = _col().update_one(
            {"email": email.strip().lower()},
            {"$set": {"trial_started_at": ts}},
        )
        return result.modified_count > 0
    except Exception:
        return False


def set_paid(email: str, paid: bool = True) -> bool:
    fp = auth_file_path()
    if fp:
        return _file_set_paid(fp, email, paid=paid)
    try:
        result = _col().update_one(
            {"email": email.strip().lower()},
            {"$set": {"paid": paid}},
        )
        return result.modified_count > 0
    except Exception:
        return False
