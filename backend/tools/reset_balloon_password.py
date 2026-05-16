"""
Reset a balloon UI user's password directly in MongoDB (no HTTP).

Run from the backend directory:

  py -3.10 tools/reset_balloon_password.py user@gmail.com "YourNewPass1!"

Uses DATABASE.URI / MONGODB_URI from config (same as serve_balloon.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
import os

os.chdir(_BACKEND)

import config
import mongodb as db
from Security.balloon_auth import hash_password as pw_hash, validate_password_strength
from Security.balloon_auth_store import get_user, update_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset Auto Ballooning user password in MongoDB.")
    parser.add_argument("email", help="User email (login id)")
    parser.add_argument("new_password", help="New password (quoted if it has special chars)")
    args = parser.parse_args()

    config.InitConfiguration()
    dbc = config.GetConfiguration("DATABASE") or {}
    if dbc.get("URI"):
        db.Connect(uri=dbc["URI"])
    else:
        db.Connect(dbc.get("ADDRESS", "localhost"), dbc.get("PORT", 27017))

    if not db.ping():
        print("ERROR: Cannot ping MongoDB. Check DATABASE.URI / Atlas network access.")
        return 1

    email = args.email.strip().lower()
    err = validate_password_strength(args.new_password)
    if err:
        print(f"ERROR: {err}")
        return 1

    if not get_user(email):
        print(f"ERROR: No user with email {email}")
        return 1

    if not update_password(email, pw_hash(args.new_password), is_temp_password=False):
        print("ERROR: Failed to update password.")
        return 1

    print(f"OK: Password updated for {email} (is_temp_password cleared).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
