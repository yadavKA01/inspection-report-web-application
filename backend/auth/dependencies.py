"""
FastAPI dependency functions.

Usage in route signatures:

    from auth.dependencies import get_current_user, require_super_admin, require_engineer

    @router.get("/something")
    async def my_route(current_user: User = Depends(get_current_user)):
        ...

    @router.post("/admin/...")
    async def admin_route(current_user: User = Depends(require_super_admin)):
        ...
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from auth.database import get_db
from auth.models import Organization, RoleEnum, User
from auth.utils import decode_access_token

# Strict bearer — raises 403 if header missing
_bearer = HTTPBearer(auto_error=True)

# Optional bearer — returns None if header missing (used on business endpoints
# so the old static UI continues to work without a token)
_optional_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Core dependency: decode JWT → load User from DB
# ---------------------------------------------------------------------------
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """
    Validate the JWT from the Authorization: Bearer <token> header.
    Returns the User ORM object for the authenticated caller.

    Raises HTTP 401 on any auth failure.
    """
    token = credentials.credentials
    payload = decode_access_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token.")

    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer exists.",
        )

    return user


# ---------------------------------------------------------------------------
# Role-enforcing wrappers
# ---------------------------------------------------------------------------
def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    """Only super_admin may pass. HTTP 403 otherwise."""
    if current_user.role != RoleEnum.super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required.",
        )
    return current_user


def require_engineer(current_user: User = Depends(get_current_user)) -> User:
    """
    Both engineers AND super_admin are allowed through (super_admin can test
    any endpoint). If you need strict engineer-only, swap the condition.
    """
    return current_user


# ---------------------------------------------------------------------------
# Tenant-aware DB session helper (not a FastAPI dependency itself, but used
# by route handlers to attach the current user's tenant_id to activity logs)
# ---------------------------------------------------------------------------
def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Like get_current_user but never raises — returns None when no/invalid token.

    Used on detect / export / extract endpoints so:
      - Old static UI (no token)  → still works, activity not logged
      - React frontend (JWT)      → works, activity logged with tenant_id
    """
    if not credentials:
        return None
    payload = decode_access_token(credentials.credentials)
    if not payload:
        return None
    user_id = payload.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter_by(id=user_id).first()


def get_tenant_id(user: User) -> str | None:
    """Return the caller's tenant_id, or None for super_admin."""
    if user.role == RoleEnum.super_admin:
        return None
    return user.tenant_id


# ---------------------------------------------------------------------------
# Subscription / Trial helpers
# ---------------------------------------------------------------------------

def check_tenant_access(org: Optional[Organization], db: Session) -> bool:
    """
    Return True if the tenant has active access, False if trial expired.

    Rules:
      - org is None            → True  (super admin or no-org user)
      - subscription_status is None → True  (legacy tenant, pre-trial system)
      - subscription_status == 'active' → True
      - subscription_status == 'trial'  → True while utcnow <= trial_end_date,
                                          then auto-expires to 'expired' and returns False
      - subscription_status == 'expired' → False
    """
    if org is None:
        return True

    # Legacy tenant — created before the trial system, treat as active
    if org.subscription_status is None:
        return True

    if org.subscription_status == "active":
        return True

    if org.subscription_status == "trial":
        trial_end = org.trial_end_date
        if trial_end is None:
            return True  # No end date set — treat as unlimited trial

        now = datetime.now(timezone.utc)
        # Normalise naive datetime (DB returned without tz) to UTC
        if trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)

        if now <= trial_end:
            return True

        # Trial has lapsed — mark as expired
        org.subscription_status = "expired"
        org.is_active = False
        try:
            db.commit()
        except Exception:
            db.rollback()
        return False

    # 'expired' or any unrecognised status
    return False


_TRIAL_EXPIRED_RESPONSE = {
    "error": "TRIAL_EXPIRED",
    "message": "Your 7-day free trial has expired. Please upgrade to continue.",
}


def require_subscription_if_auth(
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    For endpoints that accept an optional JWT (old static UI + React frontend).

    - Unauthenticated caller → pass through (legacy UI support)
    - super_admin            → pass through
    - engineer with expired trial → HTTP 403 TRIAL_EXPIRED
    """
    if current_user is None or current_user.role == RoleEnum.super_admin:
        return current_user

    org = db.query(Organization).filter_by(tenant_id=current_user.tenant_id).first()
    if not check_tenant_access(org, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_TRIAL_EXPIRED_RESPONSE,
        )
    return current_user


def require_active_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """
    For endpoints that require a valid JWT AND an active subscription.

    - super_admin → always allowed
    - engineer with expired trial → HTTP 403 TRIAL_EXPIRED
    """
    if current_user.role == RoleEnum.super_admin:
        return current_user

    org = db.query(Organization).filter_by(tenant_id=current_user.tenant_id).first()
    if not check_tenant_access(org, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_TRIAL_EXPIRED_RESPONSE,
        )
    return current_user
