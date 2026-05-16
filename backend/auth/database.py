"""
PostgreSQL (Neon) database connection via SQLAlchemy.

All auth tables (organizations, users, activities) live in this database.
The existing MongoDB connection in serve_balloon.py is untouched.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

# ---------------------------------------------------------------------------
# Connection string
# Override via environment variable DATABASE_URL to avoid hardcoding in prod.
# channel_binding=require is a Neon/SCRAM requirement; psycopg2 >= 2.9.3 supports it.
# ---------------------------------------------------------------------------
NEON_DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip()
if not NEON_DATABASE_URL:
    print("[auth] WARNING: DATABASE_URL is not set — PostgreSQL auth will not connect.")

engine = create_engine(
    NEON_DATABASE_URL or "postgresql://127.0.0.1:5432/neondb",
    pool_pre_ping=True,   # verify connection before each use
    pool_size=5,
    max_overflow=10,
    echo=False,           # set True to log all SQL (development only)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ---------------------------------------------------------------------------
# FastAPI dependency — yields a DB session per request
# ---------------------------------------------------------------------------
def get_db():
    """Yield a SQLAlchemy session; always close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Table initialisation + super-admin seeding (called once at startup)
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create all tables if they don't exist and seed the super admin."""
    # Import models so SQLAlchemy sees their metadata before create_all
    from auth import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _seed_super_admin()


def _seed_super_admin() -> None:
    """
    Create the one-and-only super admin if none exists yet.

    Credentials come from environment variables:
      SUPER_ADMIN_EMAIL    (default: admin@smorx.ai)
      SUPER_ADMIN_PASSWORD (default: auto-generated, printed once to console)
    """
    from auth.models import User, RoleEnum
    from auth.utils import hash_password, generate_temp_password

    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(role=RoleEnum.super_admin).first()
        if existing:
            return  # super admin already exists

        email = os.environ.get("SUPER_ADMIN_EMAIL", "admin@smorx.ai").strip().lower()
        raw_password = os.environ.get("SUPER_ADMIN_PASSWORD", "").strip()
        is_temp = False

        if not raw_password:
            raw_password = generate_temp_password()
            is_temp = True
            print(
                f"\n[auth] ⚠  No SUPER_ADMIN_PASSWORD set — generated one-time password:\n"
                f"          Email   : {email}\n"
                f"          Password: {raw_password}\n"
                f"          Change this immediately after first login.\n"
            )

        admin = User(
            name="Super Admin",
            email=email,
            password_hash=hash_password(raw_password),
            role=RoleEnum.super_admin,
            tenant_id=None,
            is_temp_password=is_temp,
        )
        db.add(admin)
        db.commit()
        print(f"[auth] Super admin created → {email}")
    except Exception as exc:
        db.rollback()
        print(f"[auth] WARNING: Could not seed super admin: {exc}")
    finally:
        db.close()
