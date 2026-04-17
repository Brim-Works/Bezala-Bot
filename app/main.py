import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, init_db
from app.models import ProcessedMessage, ScanRun

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
    # Scheduler kopplas in i Fas 5
    yield
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
            "drive_link": r.drive_link,
            "status": r.status,
            "error_message": r.error_message,
        }
        for r in rows
    ]


@app.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    total = db.query(func.count(ProcessedMessage.id)).scalar() or 0
    ok = (
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
    last_run = (
        db.query(ScanRun).order_by(desc(ScanRun.started_at)).first()
    )
    return {
        "total": total,
        "saved": ok,
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
