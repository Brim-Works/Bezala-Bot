"""C49 - Netvisor-status per kalenderresa (egen fil/tabell, krockar inte med
andra migreringar). Tabellen skapas av Base.metadata.create_all vid start
(modulen importeras av app/routers/netvisor.py som main.py laddar).

status:
  * not_created     - inget skapat i Netvisor (samma som ingen rad)
  * created_manual  - Mikko har skapat reseräkningen för hand i Netvisor
  * created_api     - Bezala Bot skapade den via API (NetvisorClient.send_trip)

En resa har högst en rad (unik trip_id) = högst en aktiv Netvisor-rad.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db import Base

NETVISOR_STATUSES = ("not_created", "created_manual", "created_api")
CREATED_STATUSES = ("created_manual", "created_api")


class NetvisorReport(Base):
    __tablename__ = "netvisor_reports"
    __table_args__ = (UniqueConstraint("trip_id", name="uq_netvisor_reports_trip"),)

    id = Column(Integer, primary_key=True)
    # calendar_trips.id (ingen FK: en avvisad/borttagen resa ska inte radera
    # spåret av en reseräkning som redan finns i Netvisor).
    trip_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="not_created")
    netvisor_report_id = Column(String(64), nullable=True)
    note = Column(Text, nullable=True)
    # 'mcp' | 'api' | 'seed'
    source = Column(String(16), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )
