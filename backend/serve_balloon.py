"""
Standalone YOLO detection API + static UI (does NOT use main.py).

Use this when another process on :9000 causes 404s or wrong `main` imports.

  cd AI_Engine
  python backend/serve_balloon.py

Repo layout: frontend/ (static UI) and backend/ (API + pipeline).

Then open the URL printed (default http://127.0.0.1:10000).

Returns JSON your frontend / .NET / Java can use to draw balloon circles:
  - detections[].bbox, class_name, confidence
  - drawing_annotations[] with id, BBox, TextPos (center), AnnotationType

Env:
  BALLOON_UI_PORT=10000   (optional)
  BALLOON_UI_HOST=127.0.0.1
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from pydantic import BaseModel, Field

_BACKEND_DIR = Path(__file__).resolve().parent
_APP_ROOT = _BACKEND_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent.parent

# Load .env from the backend directory (no-op if file or package is missing)
try:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND_DIR / ".env")
except ImportError:
    pass
os.chdir(_BACKEND_DIR)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
for _p in ("Modules", "Dependencies", "Resources", ".Temp"):
    _d = str(_BACKEND_DIR / _p)
    if _d not in sys.path:
        sys.path.append(_d)

import config
import mongodb as db
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook

# ── Auth system (PostgreSQL / JWT / multi-tenant) ─────────────────────────
from auth.database import get_db, init_db
from auth.dependencies import (
    check_tenant_access,
    get_current_user,
    get_optional_user,
    require_active_subscription,
    require_subscription_if_auth,
)
from auth.models import Activity, Organization, RoleEnum, User
from auth.routes import router as auth_router
from auth.admin_routes import router as admin_router
from auth.session_routes import router as session_router
from sqlalchemy.orm import Session

config.InitConfiguration()
_Db = config.GetConfiguration("DATABASE")
if _Db:
    if _Db.get("URI"):
        db.Connect(uri=_Db["URI"])
    else:
        db.Connect(_Db.get("ADDRESS", "localhost"), _Db.get("PORT", 27017))
    if db.ping():
        print("[mongodb] Ping OK — database is reachable.")
    else:
        print(
            "[mongodb] WARNING: Ping failed. Check DATABASE.URI / MONGODB_URI, Atlas IP allowlist, "
            "and database user password (URL-encode special characters in the URI)."
        )
else:
    print(
        "[mongodb] WARNING: No DATABASE section in config — MongoDB not connected. "
        "Set DATABASE.URI (or ADDRESS/PORT) in config to enable database features."
    )

# Heavy deps (torch, ultralytics) — import lazily so Render binds $PORT before loading YOLO.
_tasks_mod = None


def _tasks():
    global _tasks_mod
    if _tasks_mod is None:
        from AutoBallooning import tasks as _tasks_mod
    return _tasks_mod


_UPLOAD_ROOT = _BACKEND_DIR / ".Temp" / "balloon_ui_uploads"
_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


def _resolve_ui_dir() -> Path:
    """
    Prefer frontend_padma/frontend (repo dev UI) when present so edits apply without copying
    to Resources. Set BALLOON_USE_BUNDLED_UI=1 to force Resources/balloon_ui.
    """
    fp = _REPO_ROOT / "frontend_padma" / "frontend"
    rs = _BACKEND_DIR / "Resources" / "balloon_ui"
    bundled = os.environ.get("BALLOON_USE_BUNDLED_UI", "").strip().lower() in ("1", "true", "yes")
    if bundled and (rs / "index.html").is_file():
        return rs
    if (fp / "index.html").is_file():
        return fp
    if (rs / "index.html").is_file():
        return rs
    return _APP_ROOT / "frontend"


_UI_DIR = _resolve_ui_dir()


def _cors_allow_origins() -> list[str]:
    """Explicit origins so credentialed cookies work; * is invalid with credentials."""
    raw = os.environ.get("BALLOON_CORS_ORIGINS", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    default_ports = "3000,10000,9090," + os.environ.get("BALLOON_UI_PORT", "10000").strip()
    ports = sorted({p.strip() for p in default_ports.replace(" ", "").split(",") if p.strip()})
    out: list[str] = []
    for p in ports:
        for host in ("http://127.0.0.1", "http://localhost"):
            out.append(f"{host}:{p}")
    return out or ["http://127.0.0.1:10000"]


app = FastAPI(title="SmorX Balloon — detection API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register auth routers ──────────────────────────────────────────────────
app.include_router(auth_router)    # /auth/login, /auth/change-password, etc.
app.include_router(admin_router)   # /admin/organizations, /admin/engineers, etc.
app.include_router(session_router) # /activities/save, /activities, /activities/{id}


@app.on_event("startup")
def _startup():
    """Initialise PostgreSQL tables and seed super admin on first run."""
    if not os.environ.get("DATABASE_URL", "").strip():
        print("[auth] Skipping PostgreSQL init — set DATABASE_URL for login/admin.")
        return
    try:
        init_db()
        print("[auth] PostgreSQL tables ready.")
    except Exception as exc:
        print(f"[auth] WARNING: DB init failed — {exc}")

_STATIC_DIR = _UI_DIR / "static"
if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR)), name="balloon_assets")

print(f"[serve_balloon] UI: {_UI_DIR.resolve()}")
print("[serve_balloon] Open the app using ONE host only — e.g. http://127.0.0.1:9090")


class ExtractBalloonTextBody(BaseModel):
    """Client-sent crop (e.g. after drawing a manual box) for the same vision extract as auto-detect."""

    crop_jpeg_base64: str = Field(..., description="JPEG as data URL or raw base64")
    class_name: str = "Manual"


class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str



def _log_activity(
    db: Session,
    user: Optional[User],
    action_type: str,
    metadata: dict | None = None,
) -> None:
    """
    Persist an activity record to PostgreSQL.

    Engineers are stored under their own tenant_id.
    Super admin and unauthenticated (old UI) calls are silently skipped.
    Errors are swallowed so they never interrupt the main request.
    """
    if user is None or user.role == RoleEnum.super_admin or not user.tenant_id:
        return
    try:
        activity = Activity(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type=action_type,
            action_metadata=metadata or {},
        )
        db.add(activity)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[auth] WARNING: Failed to log activity '{action_type}': {exc}")


def _html_no_cache(path: Path) -> FileResponse:
    resp = FileResponse(str(path), media_type="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _bbox_tblr_key(det: dict) -> tuple[float, float]:
    """Sort key: top→bottom, then left→right using bbox top-left (y1, x1).

    Matches natural reading order on drawings better than center when boxes sit on one horizontal band.
    """
    bb = det.get("bbox")
    if not bb or len(bb) < 4:
        return (1e30, 1e30)
    x1, y1, x2, y2 = bb[0], bb[1], bb[2], bb[3]
    return (float(y1), float(x1))


def _reorder_detection_payload_tblr(payload: dict) -> None:
    """Align `detections` and `detections_full` to reading order (uses full-res boxes when both exist)."""
    dets = payload.get("detections") or []
    full = payload.get("detections_full")
    if not dets and not full:
        return
    if full is not None and len(full) == len(dets) and len(full) > 0:
        idx = sorted(range(len(full)), key=lambda i: _bbox_tblr_key(full[i]))
        payload["detections"] = [dets[i] for i in idx]
        payload["detections_full"] = [full[i] for i in idx]
    elif dets:
        payload["detections"] = sorted(dets, key=_bbox_tblr_key)
        if full is None:
            payload["detections_full"] = list(payload["detections"])


def _drawing_annotations_from_detections(detections: list) -> list:
    out = []
    for i, d in enumerate(detections or [], start=1):
        bb = d.get("bbox")
        if not bb or len(bb) < 4:
            continue
        x1, y1, x2, y2 = bb[0], bb[1], bb[2], bb[3]
        out.append(
            {
                "id": i,
                "AnnotationType": d.get("class_name") or "Dimensions",
                "BBox": [int(x1), int(y1), int(x2), int(y2)],
                "TextPos": [int((x1 + x2) / 2), int((y1 + y2) / 2)],
            }
        )
    return out


def _imread_bgr(path: str):
    """
    Load BGR image from disk. Uses imdecode + fromfile so Unicode paths work on Windows
    (cv2.imread often fails for non-ASCII paths and can mis-read some PNG modes).
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = np.fromfile(str(p), dtype=np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if img is None:
            img = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
            if img is not None and len(img.shape) == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            elif img is not None and len(img.shape) == 3 and img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        if img is not None:
            return img
    except Exception:
        pass
    return cv2.imread(str(p))


def _parse_extraction_json(text: str) -> dict[str, str]:
    """Parse nominal_value, tolerance, others from vision model output."""
    out = {"nominal_value": "", "tolerance": "", "others": ""}
    if not text or not str(text).strip():
        return out
    t = str(text).strip()
    if t.startswith("VISION_LLM_FAILED"):
        return out
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            out["nominal_value"] = str(data.get("nominal_value", "") or "").strip()
            out["tolerance"] = str(data.get("tolerance", "") or "").strip()
            out["others"] = str(data.get("others", "") or "").strip()
            return out
    except json.JSONDecodeError:
        pass
    out["others"] = t[:2000]
    return out


def _bgr_from_jpeg_data_url_or_b64(s: str) -> Optional[np.ndarray]:
    raw = (s or "").strip()
    if not raw:
        return None
    if raw.startswith("data:"):
        i = raw.find(",")
        if i >= 0:
            raw = raw[i + 1 :]
    try:
        data = base64.standard_b64decode(raw)
    except Exception:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def _extract_one_crop_bgr_llm(bgr: np.ndarray, class_name: str) -> dict[str, str]:
    """Same JSON extraction as _extract_detection_text_llm for a single BGR crop."""
    nominal_value = ""
    tolerance = ""
    others = ""
    if bgr is None or not getattr(bgr, "size", 0):
        return {"nominal_value": nominal_value, "tolerance": tolerance, "others": others}
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    cls = (class_name or "").strip()
    if ok:
        prompt = (
            f"YOLO class: {cls or 'unknown'}.\n"
            "This image is one detection crop from an engineering drawing.\n"
            'Return ONLY valid JSON (no markdown) with exactly these keys:\n'
            '{"nominal_value":"","tolerance":"","others":""}\n'
            "Rules:\n"
            "- For dimensions: put the main numeric value in nominal_value, ± tolerance in tolerance, "
            "units or extra text in others if needed.\n"
            "- For title blocks, notes, tables, nomenclature: put readable text in others; "
            "leave nominal_value and tolerance empty if not applicable.\n"
            "- Use empty strings where nothing applies."
        )
        try:
            val = _tasks()._vision_llm_message(
                buf.tobytes(), prompt, max_tokens=400, temperature=0.0, top_p=1.0
            )
            parsed = _parse_extraction_json(val or "")
            nominal_value = parsed["nominal_value"]
            tolerance = parsed["tolerance"]
            others = parsed["others"]
        except Exception:
            pass
    return {"nominal_value": nominal_value, "tolerance": tolerance, "others": others}


def _crop_image_data_url(bgr, max_side: int = 320, min_side: int = 48) -> str:
    """
    Encode a BGR bbox crop as a data URL for the Others column (embedded in JSON as crop_preview_base64).
    Large crops are shrunk; very small YOLO boxes are upscaled so the thumbnail is visible (not an empty box).
    """
    if bgr is None or not getattr(bgr, "size", 0):
        return ""
    h, w = bgr.shape[:2]
    if h < 1 or w < 1:
        return ""
    # Upscale tiny crops so the UI shows a real image, not a blank sliver
    if min(h, w) < min_side:
        s = min_side / min(h, w)
        nw = max(1, int(round(w * s)))
        nh = max(1, int(round(h * s)))
        bgr = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        h, w = bgr.shape[:2]
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        nw = max(1, int(round(w * s)))
        nh = max(1, int(round(h * s)))
        bgr = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if ok:
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
    ok, buf = cv2.imencode(".png", bgr)
    if ok:
        return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
    return ""


def _exact_crop_jpeg_data_url(bgr, max_side: int = 8192) -> str:
    """
    Encode the bbox crop as JPEG at ~full resolution — no thumbnail downscale — for Save.
    Only scales down if a side exceeds max_side (safety for huge drawings).
    """
    if bgr is None or not getattr(bgr, "size", 0):
        return ""
    h, w = bgr.shape[:2]
    if h < 1 or w < 1:
        return ""
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        nw = max(1, int(round(w * s)))
        nh = max(1, int(round(h * s)))
        bgr = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        ok, buf = cv2.imencode(".png", bgr)
        if ok:
            return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _extract_detection_text_llm(image_path: str, detections: list) -> list:
    """
    Vision LLM on each YOLO bbox crop only (never the full sheet). Returns structured fields
    plus a JPEG data URL thumbnail of the crop for the UI.
    """
    img = _imread_bgr(image_path)
    if img is None:
        return []
    h, w = img.shape[:2]
    items = []
    for i, d in enumerate(detections or [], start=1):
        bb = d.get("bbox") or []
        if len(bb) < 4:
            continue
        cls = (d.get("class_name") or "").strip()
        x1, y1, x2, y2 = [int(v) for v in bb[:4]]
        # Exact YOLO box (same as green rectangle in full image space) — no padding.
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(x1 + 1, min(w, x2))
        y2 = max(y1 + 1, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        crop = img[y1:y2, x1:x2]
        crop_preview_base64 = _crop_image_data_url(crop)
        crop_save_base64 = _exact_crop_jpeg_data_url(crop)
        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        nominal_value = ""
        tolerance = ""
        others = ""
        if ok:
            prompt = (
                f"YOLO class: {cls or 'unknown'}.\n"
                "This image is one detection crop from an engineering drawing.\n"
                'Return ONLY valid JSON (no markdown) with exactly these keys:\n'
                '{"nominal_value":"","tolerance":"","others":""}\n'
                "Rules:\n"
                "- For dimensions: put the main numeric value in nominal_value, ± tolerance in tolerance, "
                "units or extra text in others if needed.\n"
                "- For title blocks, notes, tables, nomenclature: put readable text in others; "
                "leave nominal_value and tolerance empty if not applicable.\n"
                "- Use empty strings where nothing applies."
            )
            try:
                val = _tasks()._vision_llm_message(
                    buf.tobytes(), prompt, max_tokens=400, temperature=0.0, top_p=1.0
                )
                parsed = _parse_extraction_json(val or "")
                nominal_value = parsed["nominal_value"]
                tolerance = parsed["tolerance"]
                others = parsed["others"]
            except Exception:
                pass
        items.append(
            {
                "balloon_number": i,
                "class_name": cls,
                "confidence": d.get("confidence", ""),
                "nominal_value": nominal_value,
                "tolerance": tolerance,
                "others": others,
                "bbox_pixels": [x1, y1, x2, y2],
                "crop_preview_base64": crop_preview_base64,
                "crop_save_base64": crop_save_base64,
            }
        )
    return items


@app.get("/health")
async def health():
    return {"ok": True, "service": "serve_balloon", "port_hint": "default 10000"}


@app.get("/api/diagnostics")
async def api_diagnostics():
    """Quick checks: DB reachability and which UI folder is served."""
    return {
        "ok": True,
        "database_configured": bool(_Db),
        "mongodb_ping": db.ping(),
        "ui_path": str(_UI_DIR.resolve()),
    }


@app.get("/")
async def root_redirect():
    return RedirectResponse("/app")


@app.get("/login")
async def login_page():
    return RedirectResponse("/app")


@app.get("/payment")
async def payment_page():
    p = _UI_DIR / "payment.html"
    if not p.is_file():
        raise HTTPException(500, "Missing payment.html")
    return _html_no_cache(p)


@app.get("/change-password")
async def change_password_page():
    return RedirectResponse("/app")


@app.get("/app")
async def app_page():
    index = _UI_DIR / "index.html"
    if not index.is_file():
        return JSONResponse(status_code=500, content={"ok": False, "error": "Missing index.html"})
    return _html_no_cache(index)


@app.get("/admin")
async def admin_page():
    p = _UI_DIR / "admin.html"
    if not p.is_file():
        raise HTTPException(500, "Missing admin.html")
    return _html_no_cache(p)


@app.get("/inspection-report")
async def inspection_report_page():
    p = _UI_DIR / "inspection_report.html"
    if not p.is_file():
        raise HTTPException(500, "Missing inspection_report.html")
    return _html_no_cache(p)


@app.post("/api/v1/detect")
async def api_detect(
    file: UploadFile = File(...),
    current_user: User = Depends(require_subscription_if_auth),
    pg: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(422, "No filename")

    suffix = Path(file.filename).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
    if suffix not in allowed:
        raise HTTPException(400, f"Unsupported type {suffix}. Use PDF or image.")

    job = uuid.uuid4().hex
    work = _UPLOAD_ROOT / job
    work.mkdir(parents=True, exist_ok=True)
    dest = work / f"input{suffix}"

    try:
        with dest.open("wb") as buf:
            shutil.copyfileobj(file.file, buf)

        payload, err = _tasks().run_drawing_yolo_detection(str(dest), str(work), file.filename)
        if err:
            return JSONResponse(status_code=400, content={"ok": False, "error": err})

        _reorder_detection_payload_tblr(payload)
        dets = payload.get("detections") or []
        payload["drawing_annotations"] = _drawing_annotations_from_detections(dets)
        # Same raster file YOLO used (do not call PdfToPreprocessedImage twice — can desync pixels vs boxes).
        extract_path = payload.get("infer_image_path") or str(dest)
        # Use full-resolution bboxes for crops (detections may be scaled for preview only).
        dets_for_crop = payload.get("detections_full") or dets
        payload["balloon_items"] = _extract_detection_text_llm(extract_path, dets_for_crop)
        payload["weights_path"] = _tasks().get_yolo_weights_path_loaded()

        # ── Activity log (tenant-scoped) ──────────────────────────────────
        _log_activity(
            pg,
            current_user,
            action_type="drawing_upload",
            metadata={
                "filename": file.filename,
                "detection_count": len(dets),
                "balloon_count": len(payload.get("balloon_items") or []),
            },
        )

        return JSONResponse(
            content={
                "ok": True,
                "version": 1,
                "filename": file.filename,
                "detection": payload,
            }
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post("/api/v1/extract-balloon-text")
async def api_extract_balloon_text(
    body: ExtractBalloonTextBody,
    current_user: User = Depends(require_subscription_if_auth),
    pg: Session = Depends(get_db),
):
    """
    Run vision LLM on a single crop (manual balloon box). Fills nominal_value / tolerance / others
    like automatic detection.
    """
    bgr = _bgr_from_jpeg_data_url_or_b64(body.crop_jpeg_base64)
    if bgr is None or not getattr(bgr, "size", 0):
        raise HTTPException(status_code=400, detail="Invalid or empty crop image")
    out = _extract_one_crop_bgr_llm(bgr, body.class_name)
    _log_activity(pg, current_user, action_type="balloon_text_extract", metadata={"class_name": body.class_name})
    return {"ok": True, "extract": out}


@app.post("/api/v1/export-excel")
async def api_export_excel(
    request: Request,
    current_user: User = Depends(require_subscription_if_auth),
    pg: Session = Depends(get_db),
):
    payload = await request.json()
    detection = payload.get("detection") or {}
    filename = payload.get("filename") or "drawing"

    wb = Workbook()
    # ws_meta = wb.active
    # ws_meta.title = "summary"
    # ws_meta.append(["filename", filename])
    # ws_meta.append(["count", detection.get("count", 0)])
    # ws_meta.append(["width", detection.get("width", "")])
    # ws_meta.append(["height", detection.get("height", "")])
    # ws_meta.append(["input_kind", detection.get("input_kind", "")])
    # ws_meta.append(["weights_path", detection.get("weights_path", "")])

    # ws_det = wb.create_sheet("detections")
    # ws_det.append(["id", "class_name", "confidence", "x1", "y1", "x2", "y2"])
    # for idx, d in enumerate(detection.get("detections") or [], start=1):
    #     bb = d.get("bbox") or [None, None, None, None]
    #     ws_det.append(
    #         [
    #             idx,
    #             d.get("class_name", ""),
    #             d.get("confidence", ""),
    #             bb[0] if len(bb) > 0 else "",
    #             bb[1] if len(bb) > 1 else "",
    #             bb[2] if len(bb) > 2 else "",
    #             bb[3] if len(bb) > 3 else "",
    #         ]
    #     )

    # ws_ann = wb.create_sheet("balloons")
    # ws_ann.append(["id", "AnnotationType", "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "text_x", "text_y"])
    # for a in detection.get("drawing_annotations") or []:
    #     bb = a.get("BBox") or [None, None, None, None]
    #     tp = a.get("TextPos") or [None, None]
    #     ws_ann.append(
    #         [
    #             a.get("id", ""),
    #             a.get("AnnotationType", ""),
    #             bb[0] if len(bb) > 0 else "",
    #             bb[1] if len(bb) > 1 else "",
    #             bb[2] if len(bb) > 2 else "",
    #             bb[3] if len(bb) > 3 else "",
    #             tp[0] if len(tp) > 0 else "",
    #             tp[1] if len(tp) > 1 else "",
    #         ]
    #     )

    ws_items = wb.active
    ws_items.title = "balloon_items"
    ws_items.append(
        ["balloon_number", "class_name", "nominal_value", "tolerance", "others"]
    )
    for it in detection.get("balloon_items") or []:
        ws_items.append(
            [
                it.get("balloon_number", ""),
                it.get("class_name", ""),
                # it.get("confidence", ""),  # excluded from export
                it.get("nominal_value", ""),
                it.get("tolerance", ""),
                it.get("others", "") or it.get("detected_text", ""),
            ]
        )

    buff = BytesIO()
    wb.save(buff)
    xlsx_name = f"AutoBallooning_{Path(filename).stem}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{xlsx_name}"'}

    # ── Activity log ───────────────────────────────────────────────────────
    _log_activity(
        pg,
        current_user,
        action_type="excel_export",
        metadata={
            "filename": filename,
            "xlsx_name": xlsx_name,
            "row_count": len(detection.get("balloon_items") or []),
        },
    )

    return Response(
        content=buff.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Activity query endpoint
# ---------------------------------------------------------------------------
@app.get("/api/v1/activities")
def api_activities(
    tenant_id: Optional[str] = None,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    pg: Session = Depends(get_db),
):
    """
    Query the activity log.

    - Engineers see only their own tenant's activity.
    - Super admin sees all tenants (or filter by ?tenant_id=xxx).
    """
    from auth.schemas import ActivityResponse

    query = pg.query(Activity)

    if current_user.role == RoleEnum.super_admin:
        # Super admin: optionally filter by tenant
        if tenant_id:
            query = query.filter(Activity.tenant_id == tenant_id)
    else:
        # Engineer: always scoped to their own tenant
        query = query.filter(Activity.tenant_id == current_user.tenant_id)

    activities = query.order_by(Activity.created_at.desc()).limit(limit).all()
    return [ActivityResponse.model_validate(a) for a in activities]


# ---------------------------------------------------------------------------
# Trial status endpoint
# ---------------------------------------------------------------------------
@app.get("/api/v1/trial-status")
def api_trial_status(
    current_user: User = Depends(get_current_user),
    pg: Session = Depends(get_db),
):
    """
    Return the current tenant's subscription / trial status.
    Engineers call this to show the trial banner in the dashboard.
    Super admin always returns active.
    """
    if current_user.role == RoleEnum.super_admin:
        return {"subscription_status": "active", "is_active": True, "days_remaining": None}

    org = pg.query(Organization).filter_by(tenant_id=current_user.tenant_id).first()
    if not org or org.subscription_status is None:
        # Legacy tenant — treat as active
        return {"subscription_status": "active", "is_active": True, "days_remaining": None}

    # Potentially expire trial
    check_tenant_access(org, pg)

    days_remaining = None
    if org.subscription_status == "trial" and org.trial_end_date:
        trial_end = org.trial_end_date
        if trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)
        delta = trial_end - datetime.now(timezone.utc)
        days_remaining = max(0, delta.days)

    return {
        "subscription_status": org.subscription_status,
        "is_active": org.is_active if org.is_active is not None else True,
        "trial_start_date": org.trial_start_date.isoformat() if org.trial_start_date else None,
        "trial_end_date": org.trial_end_date.isoformat() if org.trial_end_date else None,
        "days_remaining": days_remaining,
    }


# ---------------------------------------------------------------------------
# Payment routes — Razorpay
# ---------------------------------------------------------------------------
@app.post("/payment/create-order")
def payment_create_order(
    current_user: User = Depends(get_current_user),
    pg: Session = Depends(get_db),
):
    """
    Create a Razorpay order for the current tenant.
    Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET environment variables.
    """
    if current_user.role == RoleEnum.super_admin:
        raise HTTPException(status_code=400, detail="Super admin account does not require payment.")

    key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    amount_paise = int(os.environ.get("RAZORPAY_AMOUNT_PAISE", "99900"))  # default ₹999

    if not key_id or not key_secret:
        raise HTTPException(
            status_code=503,
            detail="Payment gateway is not configured. Contact the administrator.",
        )

    try:
        import razorpay  # noqa: F401 — installed via requirements.txt
        client = razorpay.Client(auth=(key_id, key_secret))
        order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "payment_capture": 1,
        })
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key": key_id,
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Razorpay library not installed on server.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create payment order: {exc}")


@app.post("/payment/verify")
def payment_verify(
    body: PaymentVerifyRequest,
    current_user: User = Depends(get_current_user),
    pg: Session = Depends(get_db),
):
    """
    Verify Razorpay payment signature and activate the tenant's subscription.
    """
    if current_user.role == RoleEnum.super_admin:
        raise HTTPException(status_code=400, detail="Super admin account does not require payment.")

    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not key_secret:
        raise HTTPException(status_code=503, detail="Payment gateway is not configured.")

    # Verify HMAC-SHA256 signature as per Razorpay docs
    message = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected_sig = hmac.new(
        key_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature. Payment not verified.")

    # Activate subscription
    org = pg.query(Organization).filter_by(tenant_id=current_user.tenant_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")

    org.subscription_status = "active"
    org.is_active = True
    org.payment_id = body.razorpay_payment_id
    org.payment_date = datetime.now(timezone.utc)
    pg.commit()

    return {"status": "success", "message": "Subscription activated. Welcome to SmorX.ai!"}


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("BALLOON_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("BALLOON_UI_PORT", "10000"))
    print(f"SmorX balloon UI + API  →  http://{host}:{port}/")
    print(f"App                     →  http://{host}:{port}/app")
    print(f"POST detection JSON     →  http://{host}:{port}/api/v1/detect")
    uvicorn.run(app, host=host, port=port, reload=False)
