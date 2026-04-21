import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import get_db, init_db, session_scope
from app.models import MaintenanceTask, ProcessedMessage, ScanRun
from app.scheduler import reschedule_scheduler, shutdown_scheduler, start_scheduler
from app.services.bezala_client import BezalaClient, BezalaError
from app.services.drive_client import DriveClient
from app.services.gmail_client import GmailClient
from app.services.pipeline import run_scan
from app.services.settings_service import load_settings, settings_to_dict
from app.services.trash_service import (
    drive_delete_safe,
    gmail_mark_done_safe,
    gmail_remove_label_safe,
    normalise_reason,
    restore_row,
    soft_delete_row,
)

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bezala-bot")


CLEANUP_ERRORS_TASK = "cleanup_error_messages_v1"
FIX_SKIPPED_BEZALA_TASK = "fix_skipped_bezala_to_pending_v1"


def _run_once_cleanup_errors() -> None:
    """Ta bort alla rader där status='error' — en gång per deploy-version."""
    try:
        with session_scope() as db:
            if db.query(MaintenanceTask).filter(MaintenanceTask.name == CLEANUP_ERRORS_TASK).first():
                return
            deleted = (
                db.query(ProcessedMessage)
                .filter(ProcessedMessage.status == "error")
                .delete(synchronize_session=False)
            )
            db.add(MaintenanceTask(name=CLEANUP_ERRORS_TASK))
            logger.info("Engångsrensning: tog bort %d error-rader.", deleted)
    except Exception:
        logger.exception("Engångsrensning av error-rader misslyckades.")


def _run_once_fix_skipped_bezala() -> None:
    """Migrera sparade-till-Drive-rader med bezala_upload_status='skipped' till
    'pending' så användaren kan ladda upp manuellt. Gammal logik satte
    'skipped' även för kvitton när auto-upload var av."""
    try:
        with session_scope() as db:
            if db.query(MaintenanceTask).filter(
                MaintenanceTask.name == FIX_SKIPPED_BEZALA_TASK
            ).first():
                return
            updated = (
                db.query(ProcessedMessage)
                .filter(
                    ProcessedMessage.status == "saved",
                    ProcessedMessage.bezala_upload_status == "skipped",
                )
                .update(
                    {"bezala_upload_status": "pending"},
                    synchronize_session=False,
                )
            )
            db.add(MaintenanceTask(name=FIX_SKIPPED_BEZALA_TASK))
            logger.info(
                "Engångsmigration: bezala_upload_status skipped→pending för %d rader.",
                updated,
            )
    except Exception:
        logger.exception("Engångsmigration skipped→pending misslyckades.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Startar Bezala Bot...")
    if not settings.session_secret:
        logger.warning("SESSION_SECRET saknas — sessioner är osäkra.")
    if not settings.app_password:
        logger.warning("APP_PASSWORD saknas — inloggning kommer alltid att misslyckas.")
    init_db()
    logger.info("Databas initialiserad.")
    _run_once_cleanup_errors()
    _run_once_fix_skipped_bezala()
    start_scheduler()
    yield
    shutdown_scheduler()
    logger.info("Stoppar Bezala Bot.")


app = FastAPI(title="Bezala Bot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or secrets.token_hex(32),
    session_cookie="bezala_session",
    same_site="lax",
    https_only=True,
    max_age=60 * 60 * 24 * 7,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


LOGIN_PAGE = """<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bezala Bot — Logga in</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; background: #111; color: #eee;
         display: grid; place-items: center; min-height: 100vh; margin: 0; }
  form { background: #1a1a1a; padding: 2rem; border-radius: 8px; width: 100%; max-width: 320px;
         box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
  h1 { margin: 0 0 1.5rem; font-size: 1.2rem; }
  input { width: 100%; padding: 0.6rem; background: #222; color: #eee;
          border: 1px solid #333; border-radius: 4px; box-sizing: border-box; font-size: 1rem; }
  button { width: 100%; margin-top: 1rem; padding: 0.7rem; background: #3a82f6;
           color: white; border: 0; border-radius: 4px; cursor: pointer; font-size: 1rem; }
  button:hover { background: #2f6fd8; }
  .err { color: #f66; margin: 0 0 1rem; font-size: 0.9rem; }
</style>
</head>
<body>
<form method="post" action="/login">
  <h1>Bezala Bot</h1>
  {error_block}
  <input type="password" name="password" placeholder="Lösenord" autofocus required>
  <button type="submit">Logga in</button>
</form>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=303)
    error_block = f'<p class="err">{error}</p>' if error else ""
    return HTMLResponse(LOGIN_PAGE.replace("{error_block}", error_block))


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    expected = settings.app_password
    if not expected or not secrets.compare_digest(password, expected):
        return RedirectResponse(url="/login?error=Fel+l%C3%B6senord", status_code=303)
    request.session["authenticated"] = True
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"authenticated": True}


@app.get("/health")
def health():
    return {"status": "ok"}


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    def _serve_spa():
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/")
    def spa_root():
        return _serve_spa()

    @app.get("/settings")
    def spa_settings():
        return _serve_spa()


class SettingsPayload(BaseModel):
    scan_interval_minutes: int = Field(ge=5, le=1440)
    ai_naming_enabled: bool
    auto_upload_enabled: bool
    confidence_threshold: int = Field(ge=0, le=100)
    require_attachments: bool
    exclude_promotions: bool
    exclude_social: bool
    exclude_calendar: bool
    include_senders: list[str] = Field(default_factory=list)
    exclude_senders: list[str] = Field(default_factory=list)
    exclude_subjects: list[str] = Field(default_factory=list)
    trash_auto_purge_days: int = Field(default=0, ge=0, le=365)


@app.get("/api/settings")
def get_app_settings(
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = load_settings(db)
    db.commit()
    return settings_to_dict(row)


@app.put("/api/settings")
def update_app_settings(
    payload: SettingsPayload,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = load_settings(db)
    data = payload.model_dump()
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    reschedule_scheduler(row.scan_interval_minutes)
    return settings_to_dict(row)


@app.post("/api/scan")
def trigger_scan(
    background: BackgroundTasks,
    max_results: int = 50,
    _: None = Depends(require_auth),
):
    """Kör en scanning i bakgrunden. Returnerar direkt — resultatet hamnar i ScanRun."""
    background.add_task(run_scan, max_results=max_results)
    return {"status": "started", "max_results": max_results}


@app.delete("/api/messages/errors")
def delete_error_messages(
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Rensa alla rader med status='error' så mailen kan processas om igen."""
    deleted = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.status == "error")
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("Rensade %d error-rader via API.", deleted)
    return {"deleted": deleted}


@app.get("/api/messages")
def list_messages(
    limit: int = 50,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    q = db.query(ProcessedMessage)
    if not include_deleted:
        q = q.filter(ProcessedMessage.deleted_at.is_(None))
    rows = q.order_by(desc(ProcessedMessage.processed_at)).limit(limit).all()
    return [_serialize_message(r) for r in rows]


@app.get("/api/messages/trash")
def list_trash(
    limit: int = 200,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_not(None))
        .order_by(desc(ProcessedMessage.deleted_at))
        .limit(limit)
        .all()
    )
    return [_serialize_message(r) for r in rows]


@app.get("/api/messages/trash/count")
def trash_count(
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    count = (
        db.query(func.count(ProcessedMessage.id))
        .filter(ProcessedMessage.deleted_at.is_not(None))
        .scalar()
        or 0
    )
    return {"count": int(count)}


class DeletePayload(BaseModel):
    reason: str | None = None


class BulkDeletePayload(BaseModel):
    ids: list[int] = Field(default_factory=list)
    reason: str | None = None
    permanent: bool = False
    purge_drive: bool = False


def _get_gmail_client_safe() -> GmailClient | None:
    """Instansiera Gmail-klient best-effort. Vid fel → None (operationen
    fortsätter utan Gmail-sidoeffekt)."""
    try:
        return GmailClient()
    except Exception:  # noqa: BLE001
        logger.exception("Gmail-klient kunde inte initialiseras för trash-op.")
        return None


def _get_drive_client_safe() -> DriveClient | None:
    try:
        return DriveClient()
    except Exception:  # noqa: BLE001
        logger.exception("Drive-klient kunde inte initialiseras för trash-op.")
        return None


@app.delete("/api/messages/trash")
def empty_trash(
    purge_drive: bool = False,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_not(None))
        .all()
    )
    if not rows:
        return {"deleted": 0}

    drive_file_ids = [r.drive_file_id for r in rows if r.drive_file_id]
    for row in rows:
        db.delete(row)
    db.commit()

    if purge_drive and drive_file_ids:
        drive = _get_drive_client_safe()
        if drive:
            for file_id in drive_file_ids:
                drive_delete_safe(drive, file_id)

    logger.info("Tömde papperskorgen — %d rader.", len(rows))
    return {"deleted": len(rows)}


@app.delete("/api/messages/{msg_id}")
def delete_message(
    msg_id: int,
    payload: DeletePayload | None = None,
    permanent: bool = False,
    purge_drive: bool = False,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Soft-delete default. Permanent=true → hard-delete raden.
    purge_drive=true → radera även Drive-fil (endast meningsfull vid permanent)."""
    row = db.query(ProcessedMessage).filter(ProcessedMessage.id == msg_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Meddelandet finns inte")

    reason = normalise_reason((payload.reason if payload else None))

    if permanent:
        file_id = row.drive_file_id
        message_id = row.message_id
        db.delete(row)
        db.commit()
        if purge_drive and file_id:
            drive = _get_drive_client_safe()
            if drive:
                drive_delete_safe(drive, file_id)
        logger.info("Hard-delete av msg_id=%s (purge_drive=%s)", msg_id, purge_drive)
        return {"status": "deleted", "permanent": True}

    soft_delete_row(row, reason)
    db.commit()
    gmail = _get_gmail_client_safe()
    if gmail:
        gmail_remove_label_safe(gmail, row.message_id)
    logger.info("Soft-delete av msg_id=%s (reason=%s)", msg_id, reason)
    return _serialize_message(row)


@app.post("/api/messages/{msg_id}/restore")
def restore_message(
    msg_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = db.query(ProcessedMessage).filter(ProcessedMessage.id == msg_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Meddelandet finns inte")
    if row.deleted_at is None:
        return _serialize_message(row)
    restore_row(row)
    db.commit()
    gmail = _get_gmail_client_safe()
    if gmail:
        gmail_mark_done_safe(gmail, row.message_id)
    logger.info("Restore av msg_id=%s", msg_id)
    return _serialize_message(row)


@app.post("/api/messages/bulk-delete")
def bulk_delete_messages(
    payload: BulkDeletePayload,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    ids = [int(i) for i in (payload.ids or []) if i]
    if not ids:
        return {"deleted": 0, "ids": []}

    reason = normalise_reason(payload.reason)
    rows = (
        db.query(ProcessedMessage).filter(ProcessedMessage.id.in_(ids)).all()
    )
    if not rows:
        return {"deleted": 0, "ids": []}

    drive_file_ids: list[str] = []
    gmail_ids: list[str] = []

    if payload.permanent:
        for row in rows:
            if row.drive_file_id:
                drive_file_ids.append(row.drive_file_id)
            db.delete(row)
        db.commit()
        if payload.purge_drive:
            drive = _get_drive_client_safe()
            if drive:
                for file_id in drive_file_ids:
                    drive_delete_safe(drive, file_id)
        logger.info("Bulk hard-delete av %d rader.", len(rows))
        return {"deleted": len(rows), "ids": [r.id for r in rows], "permanent": True}

    for row in rows:
        soft_delete_row(row, reason)
        if row.message_id:
            gmail_ids.append(row.message_id)
    db.commit()
    gmail = _get_gmail_client_safe()
    if gmail:
        for gid in gmail_ids:
            gmail_remove_label_safe(gmail, gid)
    logger.info("Bulk soft-delete av %d rader (reason=%s).", len(rows), reason)
    return {
        "deleted": len(rows),
        "ids": [r.id for r in rows],
        "permanent": False,
    }


def _serialize_message(r: ProcessedMessage) -> dict:
    return {
        "id": r.id,
        "message_id": r.message_id,
        "sender": r.sender,
        "subject": r.subject,
        "received_at": r.received_at.isoformat() if r.received_at else None,
        "processed_at": r.processed_at.isoformat() if r.processed_at else None,
        "file_name": r.file_name,
        "drive_file_id": r.drive_file_id,
        "drive_link": r.drive_link,
        "status": r.status,
        "error_message": r.error_message,
        "vendor": r.vendor,
        "amount": r.amount,
        "currency": r.currency,
        "receipt_date": r.receipt_date,
        "category": r.category,
        "summary": r.summary,
        "ai_confidence": r.ai_confidence,
        "bezala_transaction_id": r.bezala_transaction_id,
        "bezala_upload_status": r.bezala_upload_status,
        "bezala_error_message": r.bezala_error_message,
        "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
        "delete_reason": r.delete_reason,
    }


@app.post("/api/messages/{msg_id}/upload-to-bezala")
def upload_message_to_bezala(
    msg_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = db.query(ProcessedMessage).filter(ProcessedMessage.id == msg_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Meddelandet finns inte")
    if not row.drive_file_id or not row.file_name:
        raise HTTPException(
            status_code=400,
            detail="Meddelandet saknar Drive-fil — kan inte ladda upp till Bezala",
        )

    try:
        drive = DriveClient()
    except Exception as exc:
        logger.exception("Drive-klient kunde inte initialiseras")
        raise HTTPException(status_code=500, detail=f"Drive-init: {exc}") from exc

    try:
        bezala = BezalaClient()
    except BezalaError as exc:
        logger.exception("Bezala-klient kunde inte initialiseras")
        row.bezala_upload_status = "failed"
        row.bezala_error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Bezala-init: {exc}") from exc

    try:
        pdf_bytes = drive.download_pdf(row.drive_file_id)
        attachment = bezala.upload_attachment(row.file_name, pdf_bytes)
        description = row.summary or row.subject
        transaction = bezala.create_transaction(
            attachment_ids=[attachment.attachment_id],
            vendor=row.vendor,
            amount=row.amount,
            currency=row.currency,
            date=row.receipt_date,
            category=row.category,
            description=description,
        )
        row.bezala_transaction_id = transaction.transaction_id
        row.bezala_upload_status = "success"
        row.bezala_error_message = None
        db.commit()
        logger.info(
            "Manuell Bezala-upload klar: msg_id=%s transaction_id=%s",
            msg_id, transaction.transaction_id,
        )
        return _serialize_message(row)
    except BezalaError as exc:
        logger.exception("Manuell Bezala-upload misslyckades för msg_id=%s", msg_id)
        row.bezala_upload_status = "failed"
        row.bezala_error_message = str(exc)
        db.commit()
        raise HTTPException(
            status_code=502, detail=f"Bezala-upload misslyckades: {exc}"
        ) from exc
    finally:
        bezala.close()


@app.get("/api/runs")
def list_runs(
    limit: int = 20,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = db.query(ScanRun).order_by(desc(ScanRun.started_at)).limit(limit).all()
    return [
        {
            "id": r.id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "messages_found": r.messages_found,
            "messages_processed": r.messages_processed,
            "messages_skipped": r.messages_skipped,
            "errors": r.errors,
            "status": r.status,
            "notes": r.notes,
        }
        for r in rows
    ]


@app.get("/api/stats")
def stats(db: Session = Depends(get_db), _: None = Depends(require_auth)):
    total = db.query(func.count(ProcessedMessage.id)).scalar() or 0
    saved = (
        db.query(func.count(ProcessedMessage.id))
        .filter(ProcessedMessage.status == "saved")
        .scalar()
        or 0
    )
    errors = (
        db.query(func.count(ProcessedMessage.id))
        .filter(ProcessedMessage.status == "error")
        .scalar()
        or 0
    )
    last_run = db.query(ScanRun).order_by(desc(ScanRun.started_at)).first()
    return {
        "total": total,
        "saved": saved,
        "errors": errors,
        "last_run": {
            "started_at": last_run.started_at.isoformat() if last_run else None,
            "finished_at": last_run.finished_at.isoformat()
            if last_run and last_run.finished_at
            else None,
            "status": last_run.status if last_run else None,
            "messages_processed": last_run.messages_processed if last_run else 0,
        },
    }


# SPA-fallback — måste ligga sist så specifika routes (/, /settings, /login,
# /api/*, /health) matchas före. Returnerar index.html för alla client-side
# routes (/review, /log m.fl.) så browser-reload och deep-linking fungerar.
# Okända /api/*- och /assets/*-paths 404-ar som vanligt.
@app.get("/{spa_path:path}")
def spa_fallback(spa_path: str):
    if spa_path.startswith("api/") or spa_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not Found")
    if not FRONTEND_DIST.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(FRONTEND_DIST / "index.html")
