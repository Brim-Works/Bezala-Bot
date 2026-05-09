import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Railway ger ibland postgres:// — SQLAlchemy 2.x kräver postgresql://
db_url = settings.database_url
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine_kwargs = {"pool_pre_ping": True}
if db_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(db_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


_PROCESSED_MESSAGES_ADDITIONS = {
    "vendor": "VARCHAR(255)",
    "amount": "FLOAT",
    "currency": "VARCHAR(16)",
    "receipt_date": "VARCHAR(32)",
    "category": "VARCHAR(64)",
    "summary": "TEXT",
    "ai_confidence": "INTEGER",
    "bezala_transaction_id": "VARCHAR(255)",
    "bezala_upload_status": "VARCHAR(32)",
    "bezala_error_message": "TEXT",
    # FAS 5.1 — soft-delete
    "deleted_at": "TIMESTAMP WITH TIME ZONE",
    "delete_reason": "VARCHAR(32)",
    # FAS 5.1+ — länk-baserad PDF-hantering
    "pending_link": "VARCHAR(2048)",
}

_APP_SETTINGS_ADDITIONS = {
    # FAS 5.1 — auto-purge för papperskorg. 0 = aldrig (default).
    "trash_auto_purge_days": "INTEGER NOT NULL DEFAULT 0",
    # FAS 5.1+ — AI-konfidens + länk-fetch
    "ai_min_confidence_to_save": "INTEGER NOT NULL DEFAULT 40",
    "link_fetch_senders": "JSON NOT NULL DEFAULT '[]'",
    # Gate 1 — HTML→PDF för mailkvitton utan bilaga. Default ON.
    "html_to_pdf_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    # OAuth re-auth flags — sätts till true vid invalid_grant.
    "gmail_auth_required": "BOOLEAN NOT NULL DEFAULT FALSE",
    "drive_auth_required": "BOOLEAN NOT NULL DEFAULT FALSE",
}

_SCAN_RUNS_ADDITIONS = {
    # Gate 1.5 — Loggtransparens. Array av filtrerade mail med reason +
    # confidence. Nullable — gamla körningar har NULL (tolkas som []).
    "filtered_messages": "JSON",
}

_AI_FEEDBACK_ADDITIONS = {
    # FAS 8.1.1 — subject sparas på not_a_receipt-rader så AI:n kan
    # skilja olika mail-typer från samma avsändare.
    "subject_context": "VARCHAR(500)",
}

_INDEXES = [
    (
        "ix_processed_messages_deleted_at",
        "processed_messages",
        "deleted_at",
    ),
    # FAS 11.1 — kompositindex för datum-fönsterfrågor på resor.
    # Postgres/SQLite-syntax är identisk här.
    (
        "idx_trips_dates",
        "trips",
        "start_date, end_date",
    ),
]


def _apply_schema_migrations() -> None:
    """Idempotent: lägger till nya kolumner + index som saknas i existerande
    tabeller. Körs vid varje startup — no-op när schemat redan är uppdaterat."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    if "processed_messages" in tables:
        existing = {col["name"] for col in insp.get_columns("processed_messages")}
        with engine.begin() as conn:
            for name, col_type in _PROCESSED_MESSAGES_ADDITIONS.items():
                if name in existing:
                    continue
                try:
                    conn.execute(
                        text(
                            f"ALTER TABLE processed_messages ADD COLUMN {name} {col_type}"
                        )
                    )
                    logger.info("Lade till kolumn processed_messages.%s", name)
                except Exception:
                    logger.exception("Kunde inte lägga till kolumn %s", name)

    if "app_settings" in tables:
        existing = {col["name"] for col in insp.get_columns("app_settings")}
        with engine.begin() as conn:
            for name, col_type in _APP_SETTINGS_ADDITIONS.items():
                if name in existing:
                    continue
                try:
                    conn.execute(
                        text(f"ALTER TABLE app_settings ADD COLUMN {name} {col_type}")
                    )
                    logger.info("Lade till kolumn app_settings.%s", name)
                except Exception:
                    logger.exception("Kunde inte lägga till kolumn %s", name)

    if "scan_runs" in tables:
        existing = {col["name"] for col in insp.get_columns("scan_runs")}
        with engine.begin() as conn:
            for name, col_type in _SCAN_RUNS_ADDITIONS.items():
                if name in existing:
                    continue
                try:
                    conn.execute(
                        text(f"ALTER TABLE scan_runs ADD COLUMN {name} {col_type}")
                    )
                    logger.info("Lade till kolumn scan_runs.%s", name)
                except Exception:
                    logger.exception("Kunde inte lägga till kolumn %s", name)

    if "ai_feedback" in tables:
        existing = {col["name"] for col in insp.get_columns("ai_feedback")}
        with engine.begin() as conn:
            for name, col_type in _AI_FEEDBACK_ADDITIONS.items():
                if name in existing:
                    continue
                try:
                    conn.execute(
                        text(f"ALTER TABLE ai_feedback ADD COLUMN {name} {col_type}")
                    )
                    logger.info("Lade till kolumn ai_feedback.%s", name)
                except Exception:
                    logger.exception("Kunde inte lägga till kolumn %s", name)

    with engine.begin() as conn:
        for index_name, table_name, column_name in _INDEXES:
            if table_name not in tables:
                continue
            try:
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS {index_name} "
                        f"ON {table_name} ({column_name})"
                    )
                )
            except Exception:
                logger.exception("Kunde inte skapa index %s", index_name)


def init_db() -> None:
    from app import models  # noqa: F401 — registrerar modeller

    Base.metadata.create_all(bind=engine)
    _apply_schema_migrations()


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
