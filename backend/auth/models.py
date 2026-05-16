"""
SQLAlchemy ORM models for the auth / multi-tenant system.

Tables:
  organizations  — tenant registry
  users          — super admins + engineers
  activities     — audit log for every engineer action (tenant-scoped)
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from auth.database import Base


# ---------------------------------------------------------------------------
# Role enum
# ---------------------------------------------------------------------------
class RoleEnum(str, enum.Enum):
    super_admin = "super_admin"
    engineer = "engineer"


# ---------------------------------------------------------------------------
# Organization (tenant)
# ---------------------------------------------------------------------------
class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    # tenant_id is the stable public identifier used in every query for isolation.
    # Generated as a short UUID slug on creation.
    tenant_id = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ── Subscription / Trial fields ──────────────────────────────────────────
    # All nullable so existing tenants (created before this feature) are unaffected.
    # NULL subscription_status → treated as "active" (legacy tenant).
    trial_start_date = Column(DateTime(timezone=True), nullable=True)
    trial_end_date   = Column(DateTime(timezone=True), nullable=True)
    is_active        = Column(Boolean, default=True, nullable=True)
    # subscription_status: 'trial' | 'active' | 'expired'
    subscription_status = Column(String(20), nullable=True)
    payment_id       = Column(String(255), nullable=True)
    payment_date     = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    users = relationship("User", back_populates="organization", foreign_keys="User.tenant_id")
    activities = relationship("Activity", back_populates="organization", foreign_keys="Activity.tenant_id")


# ---------------------------------------------------------------------------
# User (super_admin or engineer)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(RoleEnum), nullable=False, default=RoleEnum.engineer)

    # tenant_id is NULL for super_admin; references organizations.tenant_id for engineers
    tenant_id = Column(String(64), ForeignKey("organizations.tenant_id"), nullable=True, index=True)

    # Temporary password flag — engineers must change on first login
    is_temp_password = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    organization = relationship("Organization", back_populates="users", foreign_keys=[tenant_id])
    activities = relationship("Activity", back_populates="user", foreign_keys="Activity.user_id")


# ---------------------------------------------------------------------------
# Activity (audit log — every engineer action is stored here)
# ---------------------------------------------------------------------------
class Activity(Base):
    __tablename__ = "activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Always set — super_admin actions are stored under "system" tenant
    tenant_id = Column(String(64), ForeignKey("organizations.tenant_id"), nullable=False, index=True)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Human-readable action label e.g. "drawing_upload", "excel_export"
    action_type = Column(String(100), nullable=False)

    # Arbitrary JSON payload (filename, detection_count, etc.)
    # Named action_metadata to avoid conflict with SQLAlchemy's reserved Base.metadata
    action_metadata = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    organization = relationship("Organization", back_populates="activities", foreign_keys=[tenant_id])
    user = relationship("User", back_populates="activities", foreign_keys=[user_id])


# ---------------------------------------------------------------------------
# DrawingSession — persisted result of one "Run auto ballooning" + save
# ---------------------------------------------------------------------------
class DrawingSession(Base):
    __tablename__ = "drawing_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Tenant isolation — always required
    tenant_id = Column(String(64), ForeignKey("organizations.tenant_id"), nullable=False, index=True)
    user_id   = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Human-readable file name from the original upload
    filename  = Column(String(500), nullable=False, default="drawing")

    # Base64 JPEG of the annotated canvas (with balloon circles drawn on it).
    # Stored as TEXT; frontend sends data:image/jpeg;base64,... string.
    drawing_preview_b64 = Column(Text, nullable=True)

    # Full detection payload (detections, balloon_items with crop thumbnails).
    # crop_save_base64 fields are stripped server-side to keep size manageable.
    extracted_data = Column(JSONB, nullable=True)

    # Flat list of balloon_items rows — used for the table/Excel view.
    excel_data = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    organization = relationship("Organization", foreign_keys=[tenant_id])
    user         = relationship("User",         foreign_keys=[user_id])
