"""
Auth routes — login, change password, forgot password, /me.

Prefix: /auth
All routes are public except /auth/me and /auth/change-password.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth.database import get_db
from auth.dependencies import get_current_user
from auth.models import User, RoleEnum
from auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    TokenResponse,
    UserResponse,
)
from auth.utils import (
    create_access_token,
    generate_temp_password,
    hash_password,
    send_temp_password_email,
    validate_password_strength,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate with email + password.

    Returns a JWT access token.
    If is_temp_password is True, the client should redirect to /change-password.
    """
    # Fetch user
    user = db.query(User).filter_by(email=body.email).first()
    if not user:
        # Generic message to prevent email enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Verify password
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Issue JWT
    token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
        tenant_id=user.tenant_id,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        requires_password_change=user.is_temp_password,
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
        tenant_id=user.tenant_id,
    )


# ---------------------------------------------------------------------------
# POST /auth/change-password   (requires valid JWT)
# ---------------------------------------------------------------------------
@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Set a new permanent password for the currently authenticated user.

    Validates:
      - new_password == confirm_password
      - password strength (≥8 chars, uppercase, lowercase, digit, special)

    Clears is_temp_password flag on success.
    """
    if body.new_password != body.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match.",
        )

    # Strength is already validated by the Pydantic schema validator,
    # but double-check here for defence in depth.
    err = validate_password_strength(body.new_password)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    current_user.password_hash = hash_password(body.new_password)
    current_user.is_temp_password = False
    db.commit()

    return {"ok": True, "message": "Password updated successfully."}


# ---------------------------------------------------------------------------
# POST /auth/forgot-password   (public)
# ---------------------------------------------------------------------------
@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Issue a new temporary password for the given email.

    Security note: Always returns a success-like message regardless of whether
    the email exists (prevents email enumeration).
    """
    print(f"FORGOT PASSWORD TRIGGERED — email: {body.email}")

    user = db.query(User).filter_by(email=body.email).first()
    if user:
        temp_pwd = generate_temp_password()
        user.password_hash = hash_password(temp_pwd)
        user.is_temp_password = True
        db.commit()
        print(f"FORGOT PASSWORD — sending email to {user.email}")
        sent = send_temp_password_email(
            to_email=user.email,
            name=user.name,
            temp_password=temp_pwd,
        )
        print(f"FORGOT PASSWORD — email sent: {sent}")
    else:
        print(f"FORGOT PASSWORD — no account found for {body.email}")

    return {
        "ok": True,
        "message": "If an account with that email exists, a temporary password has been sent.",
    }


# ---------------------------------------------------------------------------
# GET /auth/me   (requires valid JWT)
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user
