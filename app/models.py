from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint
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

    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
