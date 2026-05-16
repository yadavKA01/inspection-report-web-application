"""
Local JSON file storage for balloon auth when MongoDB is unavailable.

Enable with:
  set BALLOON_USE_FILE_AUTH=1
or set an explicit path:
  set BALLOON_AUTH_FILE=C:\\path\\balloon_users.json

Not for production multi-instance deployments (single-file, process-local).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"users": []}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {"users": []}
        if not isinstance(data, dict):
            return {"users": []}
        if "users" not in data or not isinstance(data["users"], list):
            data["users"] = []
        return data
    except Exception:
        return {"users": []}


def file_get_user(path: Path, email: str) -> Optional[dict]:
    em = email.strip().lower()
    with _lock:
        data = _load(path)
    for u in data.get("users", []):
        if isinstance(u, dict) and u.get("email") == em:
            return dict(u)
    return None


def file_list_users(path: Path) -> list:
    with _lock:
        data = _load(path)
    out = []
    for u in data.get("users", []):
        if isinstance(u, dict):
            x = {k: v for k, v in u.items() if k != "password_hash"}
            out.append(x)
    return out


def file_create_user(
    path: Path,
    email: str,
    password_hash: str,
    *,
    role: str = "user",
    firstname: str = "",
    lastname: str = "",
    is_temp_password: bool = False,
    temp_password_expires_at: Optional[float] = None,
) -> bool:
    em = email.strip().lower()
    doc = {
        "email": em,
        "password_hash": password_hash,
        "role": role,
        "firstname": firstname.strip(),
        "lastname": lastname.strip(),
        "paid": False,
        "trial_started_at": None,
        "is_temp_password": is_temp_password,
        "temp_password_expires_at": temp_password_expires_at,
        "created_at": time.time(),
    }
    with _lock:
        data = _load(path)
        for u in data.get("users", []):
            if isinstance(u, dict) and u.get("email") == em:
                return False
        data.setdefault("users", []).append(doc)
        try:
            _atomic_write(path, data)
            return True
        except Exception as exc:
            print(f"[auth_file] create_user write failed: {exc}")
            return False


def file_update_password(path: Path, email: str, new_hash: str, is_temp_password: bool = False) -> bool:
    em = email.strip().lower()
    with _lock:
        data = _load(path)
        found = False
        for u in data.get("users", []):
            if isinstance(u, dict) and u.get("email") == em:
                u["password_hash"] = new_hash
                u["is_temp_password"] = is_temp_password
                u["temp_password_expires_at"] = None
                found = True
                break
        if not found:
            return False
        try:
            _atomic_write(path, data)
            return True
        except Exception:
            return False


def file_update_trial_start(path: Path, email: str, ts: float) -> bool:
    em = email.strip().lower()
    with _lock:
        data = _load(path)
        for u in data.get("users", []):
            if isinstance(u, dict) and u.get("email") == em:
                u["trial_started_at"] = ts
                try:
                    _atomic_write(path, data)
                    return True
                except Exception:
                    return False
        return False


def file_set_paid(path: Path, email: str, paid: bool = True) -> bool:
    em = email.strip().lower()
    with _lock:
        data = _load(path)
        for u in data.get("users", []):
            if isinstance(u, dict) and u.get("email") == em:
                u["paid"] = paid
                try:
                    _atomic_write(path, data)
                    return True
                except Exception:
                    return False
        return False


def file_init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        _atomic_write(path, {"users": []})
        print(f"[auth_file] Created empty user store: {path}")
