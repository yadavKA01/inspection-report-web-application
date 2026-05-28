"""
Auth database connection via SQLAlchemy.

Production: set DATABASE_URL to a PostgreSQL (Neon) connection string.
Local dev:   if DATABASE_URL is empty, uses backend/.data/auth.sqlite automatically.
             If Neon/Postgres is unreachable, set AUTH_USE_SQLITE=1 or rely on
             BALLOON_AUTH_FALLBACK_SQLITE=1 (default) to use local SQLite.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SQLITE_PATH = _BACKEND_DIR / ".data" / "auth.sqlite"


def _sqlite_url() -> str:
    _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_SQLITE_PATH.as_posix()}"


def _postgres_reachable(url: str, timeout_sec: float = 8.0) -> bool:
    try:
        probe = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": int(timeout_sec)},
        )
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception as exc:
        print(f"[auth] PostgreSQL probe failed: {exc}")
        return False


def resolve_database_url() -> str:
    force_sqlite = os.environ.get("AUTH_USE_SQLITE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if force_sqlite:
        print("[auth] AUTH_USE_SQLITE=1 — using local SQLite auth database.")
        return _sqlite_url()

    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("sqlite:"):
        return url
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        fallback = os.environ.get("BALLOON_AUTH_FALLBACK_SQLITE", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        if fallback and not _postgres_reachable(url):
            print(
                "[auth] PostgreSQL unreachable — falling back to local SQLite "
                f"({_SQLITE_PATH}). Fix DATABASE_URL for production, or set AUTH_USE_SQLITE=1."
            )
            return _sqlite_url()
        return url
    return _sqlite_url()


DATABASE_URL: str = resolve_database_url()
IS_SQLITE: bool = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    print(f"[auth] Using local SQLite auth database → {_SQLITE_PATH}")
elif not os.environ.get("DATABASE_URL", "").strip():
    print("[auth] WARNING: DATABASE_URL is not set — PostgreSQL auth will not connect.")
else:
    print("[auth] Using PostgreSQL auth database.")

_engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if IS_SQLITE:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update({"pool_size": 5, "max_overflow": 10})

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Yield a SQLAlchemy session; always close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables if they don't exist and seed the super admin."""
    from auth import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_schema()
    _seed_super_admin()
    try:
        _sync_super_admin_password_from_env()
    except Exception as exc:
        print(f"[auth] Super admin password sync skipped: {exc}")


def _migrate_schema() -> None:
    """Add new columns to existing deployments without Alembic."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("users")}
    alters: list[str] = []
    if "username" not in existing:
        alters.append("ALTER TABLE users ADD COLUMN username VARCHAR(64)")
    if "email_verified" not in existing:
        alters.append("ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE")
    if "can_read" not in existing:
        alters.append("ALTER TABLE users ADD COLUMN can_read BOOLEAN DEFAULT TRUE")
    if "can_write" not in existing:
        alters.append("ALTER TABLE users ADD COLUMN can_write BOOLEAN DEFAULT TRUE")
    if "can_delete" not in existing:
        alters.append("ALTER TABLE users ADD COLUMN can_delete BOOLEAN DEFAULT TRUE")
    if "is_active" not in existing:
        alters.append("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE")

    if not alters:
        return

    with engine.begin() as conn:
        for ddl in alters:
            try:
                conn.execute(text(ddl))
            except Exception as exc:
                print(f"[auth] Migration note ({ddl}): {exc}")


def _seed_super_admin() -> None:
    from auth.models import RoleEnum, User
    from auth.utils import generate_temp_password, hash_password

    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(role=RoleEnum.super_admin).first()
        if existing:
            return

        email = os.environ.get("SUPER_ADMIN_EMAIL", "admin@smorx.ai").strip().lower()
        raw_password = os.environ.get("SUPER_ADMIN_PASSWORD", "").strip()
        is_temp = False

        if not raw_password:
            raw_password = generate_temp_password()
            is_temp = True
            print(
                f"\n[auth] No SUPER_ADMIN_PASSWORD set — generated one-time password:\n"
                f"          Email   : {email}\n"
                f"          Password: {raw_password}\n"
            )

        admin = User(
            name="Super Admin",
            email=email,
            username="superadmin",
            password_hash=hash_password(raw_password),
            role=RoleEnum.super_admin,
            tenant_id=None,
            is_temp_password=is_temp,
            email_verified=True,
            can_read=True,
            can_write=True,
            can_delete=True,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        print(f"[auth] Super admin created → {email}")
    except Exception as exc:
        db.rollback()
        print(f"[auth] WARNING: Could not seed super admin: {exc}")
    finally:
        db.close()


def _sync_super_admin_password_from_env() -> None:
    """
    One-time recovery: set BALLOON_RESET_SUPER_ADMIN_PASSWORD=1 on Render with
    SUPER_ADMIN_PASSWORD, redeploy, log in, then remove the flag.
    """
    flag = os.environ.get("BALLOON_RESET_SUPER_ADMIN_PASSWORD", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return
    raw_password = os.environ.get("SUPER_ADMIN_PASSWORD", "").strip()
    if not raw_password:
        print("[auth] BALLOON_RESET_SUPER_ADMIN_PASSWORD=1 but SUPER_ADMIN_PASSWORD is empty — skipped.")
        return

    from auth.models import RoleEnum, User
    from auth.utils import hash_password

    email = os.environ.get("SUPER_ADMIN_EMAIL", "admin@smorx.ai").strip().lower()
    db = SessionLocal()
    try:
        admin = db.query(User).filter_by(role=RoleEnum.super_admin).first()
        if not admin:
            admin = db.query(User).filter(User.email == email).first()
        if not admin:
            print(f"[auth] Password reset skipped — no user for {email}")
            return
        admin.password_hash = hash_password(raw_password)
        admin.is_temp_password = False
        admin.email_verified = True
        admin.is_active = True
        if not (admin.username or "").strip():
            admin.username = "superadmin"
        db.commit()
        print(
            f"[auth] Super admin password reset from env for {admin.email} — "
            "remove BALLOON_RESET_SUPER_ADMIN_PASSWORD after login."
        )
    except Exception as exc:
        db.rollback()
        print(f"[auth] WARNING: Super admin password reset failed: {exc}")
    finally:
        db.close()
