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
}


def _apply_schema_migrations() -> None:
    """Idempotent: lägger till nya kolumner som saknas i existerande tabeller."""
    insp = inspect(engine)
    if "processed_messages" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("processed_messages")}
    with engine.begin() as conn:
        for name, col_type in _PROCESSED_MESSAGES_ADDITIONS.items():
            if name in existing:
                continue
            try:
                conn.execute(
                    text(f"ALTER TABLE processed_messages ADD COLUMN {name} {col_type}")
                )
                logger.info("Lade till kolumn processed_messages.%s", name)
            except Exception:
                logger.exception("Kunde inte lägga till kolumn %s", name)


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
