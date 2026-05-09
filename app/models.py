from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db import Base


class ProcessedMessage(Base):
    """Loggar varje bearbetat Gmail-meddelande. message_id är unikt = dubblettskydd."""

    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("message_id", name="uq_processed_message_id"),)

    id = Column(Integer, primary_key=True)
    message_id = Column(String(255), nullable=False, index=True)
    thread_id = Column(String(255), nullable=True)
    sender = Column(String(512), nullable=True)
    subject = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, server_default=func.now(), nullable=False)
    file_name = Column(String(512), nullable=True)
    drive_file_id = Column(String(255), nullable=True)
    drive_link = Column(Text, nullable=True)
    status = Column(String(64), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)

    vendor = Column(String(255), nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String(16), nullable=True)
    receipt_date = Column(String(32), nullable=True)
    category = Column(String(64), nullable=True)
    summary = Column(Text, nullable=True)
    ai_confidence = Column(Integer, nullable=True)

    bezala_transaction_id = Column(String(255), nullable=True)
    bezala_upload_status = Column(String(32), nullable=True)
    bezala_error_message = Column(Text, nullable=True)

    # Soft-delete (FAS 5.1). deleted_at = NULL → aktiv rad.
    # delete_reason: manual | calendar | spam | misclassified
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    delete_reason = Column(String(32), nullable=True)

    # Länk-baserad PDF-hantering (ny): när leverantören skickar kvitto
    # bakom en klick-länk sparas URL:en här och status sätts till
    # 'needs_manual_download' tills användaren triggar /fetch-pdf.
    pending_link = Column(String(2048), nullable=True)


class SavedFile(Base):
    """Unikhetsindex för filnamn + datum (tredje dubblettskiktet)."""

    __tablename__ = "saved_files"
    __table_args__ = (UniqueConstraint("file_name", "file_date", name="uq_filename_date"),)

    id = Column(Integer, primary_key=True)
    file_name = Column(String(512), nullable=False)
    file_date = Column(String(32), nullable=False)
    drive_file_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class ScanRun(Base):
    """Logg av varje schemalagd scanning."""

    __tablename__ = "scan_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    messages_found = Column(Integer, default=0, nullable=False)
    messages_processed = Column(Integer, default=0, nullable=False)
    messages_skipped = Column(Integer, default=0, nullable=False)
    errors = Column(Integer, default=0, nullable=False)
    status = Column(String(32), default="running", nullable=False)
    notes = Column(Text, nullable=True)

    # Gate 1.5 — Loggtransparens. Array av filtrerade mail med reason +
    # confidence. NULL = gammal körning utan detaljer (tolkas som [] i API).
    filtered_messages = Column(JSON, nullable=True)


class AppSettings(Base):
    """Applikationsinställningar (singleton — id=1)."""

    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)

    scan_interval_minutes = Column(Integer, nullable=False, default=60)
    ai_naming_enabled = Column(Boolean, nullable=False, default=True)
    auto_upload_enabled = Column(Boolean, nullable=False, default=False)
    confidence_threshold = Column(Integer, nullable=False, default=90)

    require_attachments = Column(Boolean, nullable=False, default=True)
    exclude_promotions = Column(Boolean, nullable=False, default=True)
    exclude_social = Column(Boolean, nullable=False, default=True)
    exclude_calendar = Column(Boolean, nullable=False, default=True)

    include_senders = Column(JSON, nullable=False, default=list)
    exclude_senders = Column(JSON, nullable=False, default=list)
    exclude_subjects = Column(JSON, nullable=False, default=list)

    # Auto-purge för papperskorg. 0 = aldrig (default). 30/60/90 dagar
    # är tillåtna värden i UI, men fältet lagrar vilken siffra som helst.
    trash_auto_purge_days = Column(Integer, nullable=False, default=0)

    # AI-tröskel: mail med confidence lägre än detta SPARAS INTE i DB
    # (raden hoppas permanent över — mark_done sätts i Gmail). Default 40.
    ai_min_confidence_to_save = Column(Integer, nullable=False, default=40)

    # Leverantörer där Bezala Bot ignorerar bilagor och letar efter en
    # kvitto-länk i mailets body istället. Förifylld med Arlanda Express.
    link_fetch_senders = Column(JSON, nullable=False, default=list)

    # Konvertera mail-body till PDF när bilaga saknas (t.ex. Moovy,
    # Skånetrafiken). Default ON.
    html_to_pdf_enabled = Column(Boolean, nullable=False, default=True)

    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CurrencyRate(Base):
    """Cache för historiska ECB-växelkurser från frankfurter.app.
    Historiska kurser ändras inte → ingen TTL, unik (date, from, to)."""

    __tablename__ = "currency_rates"
    __table_args__ = (
        UniqueConstraint("date", "from_currency", "to_currency",
                         name="uq_currency_rate_date_pair"),
    )

    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    from_currency = Column(String(3), nullable=False)
    to_currency = Column(String(3), nullable=False)
    rate = Column(Float, nullable=False)
    fetched_at = Column(DateTime, server_default=func.now(), nullable=False)


class MaintenanceTask(Base):
    """Spår engångs-underhållsjobb som körts (kleaning, seed-data etc)."""

    __tablename__ = "maintenance_tasks"

    name = Column(String(128), primary_key=True)
    ran_at = Column(DateTime, server_default=func.now(), nullable=False)


class AiFeedback(Base):
    """FAS 8 — feedback-loop. Loggar 👍/👎 + rättelser från användaren
    så AI:n kan lära sig genom few-shot-exempel i nästa anrop.

    feedback_type: 'thumbs_up' | 'thumbs_down' | 'correction'
    field_name: 'vendor' | 'amount' | 'date' | 'category' (NULL för
    thumbs_up + thumbs_down utan specificerade fält).
    vendor_context: leverantörsnamn för indexering — gör det möjligt
    att hämta vendor-specifika few-shot-exempel.

    OBS: vi använder INTE en hård FK till processed_messages eftersom
    SQLite/Postgres-FK till en UNIQUE (icke-PK) kolumn beter sig olika.
    Föräldralösa rader (efter hard-delete) är OK — de behöver inte
    putsas bort, eftersom de fortfarande är användbara träningsdata."""

    __tablename__ = "ai_feedback"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(255), nullable=False, index=True)
    feedback_type = Column(String(20), nullable=False)
    field_name = Column(String(50), nullable=True)
    ai_value = Column(Text, nullable=True)
    correct_value = Column(Text, nullable=True)
    vendor_context = Column(String(255), nullable=True, index=True)
    created_at = Column(
        DateTime, server_default=func.now(), nullable=False, index=True,
    )
