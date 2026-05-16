"""
Pydantic request / response schemas for auth and admin endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


# ---------------------------------------------------------------------------
# Auth — request bodies
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


class ChangePasswordRequest(BaseModel):
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def validate_strength(cls, v: str) -> str:
        from auth.utils import validate_password_strength
        err = validate_password_strength(v)
        if err:
            raise ValueError(err)
        return v


class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


# ---------------------------------------------------------------------------
# Auth — response bodies
# ---------------------------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_password_change: bool
    user_id: str
    email: str
    role: str
    tenant_id: Optional[str]


class UserResponse(BaseModel):
    id: UUID
    name: str
    email: str
    role: str
    tenant_id: Optional[str]
    is_temp_password: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Admin — organization
# ---------------------------------------------------------------------------
class CreateOrganizationRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Organization name cannot be empty")
        return v.strip()


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    tenant_id: str
    created_at: datetime
    engineer_count: int = 0
    subscription_status: Optional[str] = None
    trial_end_date: Optional[datetime] = None
    is_active: Optional[bool] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Admin — engineer creation
# ---------------------------------------------------------------------------
class CreateEngineerRequest(BaseModel):
    name: str
    email: str
    tenant_id: str  # must reference an existing organization tenant_id

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()


class EngineerCreatedResponse(BaseModel):
    ok: bool = True
    user_id: str
    email: str
    tenant_id: str
    # temp_password is included here for dev/demo.
    # In production, deliver this ONLY via email, never in the HTTP response.
    temp_password: str
    message: str


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------
class ActivityResponse(BaseModel):
    id: UUID
    tenant_id: str
    user_id: UUID
    action_type: str
    action_metadata: Optional[Any]
    created_at: datetime

    model_config = {"from_attributes": True}
