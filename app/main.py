import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, init_db
from app.models import ProcessedMessage, ScanRun
from app.scheduler import shutdown_scheduler, start_scheduler
from app.services.pipeline import run_scan

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bezala-bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Startar Bezala Bot...")
    init_db()
    logger.info("Databas initialiserad.")
    start_scheduler()
    yield
    shutdown_scheduler()
    logger.info("Stoppar Bezala Bot.")


app = FastAPI(title="Bezala Bot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    @app.get("/")
    def spa_root():
        return FileResponse(FRONTEND_DIST / "index.html")


@app.post("/api/scan")
def trigger_scan(background: BackgroundTasks, max_results: int = 50):
    """Kör en scanning i bakgrunden. Returnerar direkt — resultatet hamnar i ScanRun."""
    background.add_task(run_scan, max_results=max_results)
    return {"status": "started", "max_results": max_results}


@app.get("/api/messages")
def list_messages(limit: int = 50, db: Session = Depends(get_db)):
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
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
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
def stats(db: Session = Depends(get_db)):
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
