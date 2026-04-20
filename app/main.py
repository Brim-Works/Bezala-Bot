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
from app.db import get_db, init_db
from app.models import ProcessedMessage, ScanRun
from app.scheduler import reschedule_scheduler, shutdown_scheduler, start_scheduler
from app.services.pipeline import run_scan
from app.services.settings_service import load_settings, settings_to_dict

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bezala-bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Startar Bezala Bot...")
    if not settings.session_secret:
        logger.warning("SESSION_SECRET saknas — sessioner är osäkra.")
    if not settings.app_password:
        logger.warning("APP_PASSWORD saknas — inloggning kommer alltid att misslyckas.")
    init_db()
    logger.info("Databas initialiserad.")
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


@app.get("/api/messages")
def list_messages(
    limit: int = 50,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = (
        db.query(ProcessedMessage)
        .order_by(desc(ProcessedMessage.processed_at))
        .limit(limit)
        .all()
    )
    return [
        {
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
        }
        for r in rows
    ]


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
