"""
Drawing session endpoints — save and retrieve annotated drawing results.

Routes (all require JWT):
  POST /activities/save        — save a drawing session to PostgreSQL
  GET  /activities             — list sessions for the current tenant
  GET  /activities/{id}        — full detail for one session
"""
from __future__ import annotations

import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.database import get_db
from auth.dependencies import get_current_user, require_active_subscription
from auth.models import Activity, DrawingSession, RoleEnum, User

router = APIRouter(tags=["sessions"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class SaveSessionRequest(BaseModel):
    filename: str = "drawing"
    drawing_preview_b64: Optional[str] = None   # data:image/jpeg;base64,…
    extracted_data: Optional[Any] = None         # full detection payload
    excel_data: Optional[Any] = None             # flat balloon_items list


class SessionSummaryResponse(BaseModel):
    id: str
    filename: str
    balloon_count: int
    created_at: str
    drawing_preview_b64: Optional[str] = None   # thumbnail (may be large)

    model_config = {"from_attributes": True}


class SessionDetailResponse(BaseModel):
    id: str
    filename: str
    balloon_count: int
    created_at: str
    drawing_preview_b64: Optional[str] = None
    extracted_data: Optional[Any] = None
    excel_data: Optional[Any] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip_crops(extracted_data: Any) -> Any:
    """
    Remove crop_save_base64 from every item in extracted_data to reduce DB size.
    Works whether extracted_data is a dict with 'balloon_items' or a bare list.
    """
    if not extracted_data:
        return extracted_data

    _CROP_KEYS = {"crop_save_base64", "crop_preview_base64"}

    def _clean_item(item: dict) -> dict:
        return {k: v for k, v in item.items() if k not in _CROP_KEYS}

    if isinstance(extracted_data, dict):
        result = dict(extracted_data)
        if "balloon_items" in result and isinstance(result["balloon_items"], list):
            result["balloon_items"] = [_clean_item(i) for i in result["balloon_items"]]
        return result

    if isinstance(extracted_data, list):
        return [_clean_item(i) if isinstance(i, dict) else i for i in extracted_data]

    return extracted_data


def _balloon_count(session: DrawingSession) -> int:
    """Count balloon items from excel_data (fastest) or extracted_data."""
    if session.excel_data and isinstance(session.excel_data, list):
        return len(session.excel_data)
    if session.extracted_data:
        ed = session.extracted_data
        if isinstance(ed, dict) and "balloon_items" in ed:
            return len(ed["balloon_items"])
        if isinstance(ed, list):
            return len(ed)
    return 0


def _to_summary(s: DrawingSession) -> dict:
    return {
        "id": str(s.id),
        "filename": s.filename,
        "balloon_count": _balloon_count(s),
        "created_at": s.created_at.isoformat(),
        "drawing_preview_b64": s.drawing_preview_b64,
    }


def _to_detail(s: DrawingSession) -> dict:
    return {
        "id": str(s.id),
        "filename": s.filename,
        "balloon_count": _balloon_count(s),
        "created_at": s.created_at.isoformat(),
        "drawing_preview_b64": s.drawing_preview_b64,
        "extracted_data": s.extracted_data,
        "excel_data": s.excel_data,
    }


# ---------------------------------------------------------------------------
# POST /activities/save
# ---------------------------------------------------------------------------
@router.post("/activities/save", status_code=status.HTTP_201_CREATED)
def save_session(
    body: SaveSessionRequest,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Save an annotated drawing session for the authenticated engineer."""
    if current_user.role == RoleEnum.super_admin or not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only engineers with a tenant can save sessions.",
        )

    cleaned = _strip_crops(body.extracted_data)

    session = DrawingSession(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        filename=body.filename or "drawing",
        drawing_preview_b64=body.drawing_preview_b64,
        extracted_data=cleaned,
        excel_data=body.excel_data,
    )
    db.add(session)

    # Log activity
    activity = Activity(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action_type="session_saved",
        action_metadata={
            "filename": body.filename,
            "balloon_count": _balloon_count(session),
        },
    )
    db.add(activity)

    try:
        db.commit()
        db.refresh(session)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save session: {exc}")

    return {"id": str(session.id), "message": "Session saved."}


# ---------------------------------------------------------------------------
# GET /activities  (list sessions for current tenant)
# ---------------------------------------------------------------------------
@router.get("/activities", response_model=List[dict])
def list_sessions(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(DrawingSession)

    if current_user.role == RoleEnum.super_admin:
        # Super admin sees all (no tenant filter)
        pass
    else:
        query = query.filter(DrawingSession.tenant_id == current_user.tenant_id)

    sessions = query.order_by(DrawingSession.created_at.desc()).limit(limit).all()
    return [_to_summary(s) for s in sessions]


# ---------------------------------------------------------------------------
# GET /activities/{id}
# ---------------------------------------------------------------------------
@router.get("/activities/{session_id}", response_model=dict)
def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    session = db.query(DrawingSession).filter_by(id=sid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    # Tenant isolation for engineers
    if (
        current_user.role != RoleEnum.super_admin
        and session.tenant_id != current_user.tenant_id
    ):
        raise HTTPException(status_code=404, detail="Session not found.")

    return _to_detail(session)
