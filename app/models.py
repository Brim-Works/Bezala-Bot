from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
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
