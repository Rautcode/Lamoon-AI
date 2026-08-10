"""Attendance. Both tables RLS-scoped (TenantBase).

Design: an APPEND-ONLY PUNCH LEDGER, not one mutable row per person per day.

  * People punch more than twice a day — lunch, client visits, a forgotten
    check-out corrected later. A two-column in/out row can't represent that.
  * A day's hours are DERIVED from the punches (service.day_summary), the same
    way leave balance is derived from approved requests rather than stored in
    a counter that can drift.
  * Corrections are new events, so the history of what was recorded stays
    intact — which is what you want the day someone disputes their hours.
"""
import uuid
from datetime import datetime, time

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase


class AttendanceEvent(TenantBase):
    __tablename__ = "attendance_events"
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(4))  # "in" | "out"
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: ess | hr — who recorded it. HR corrections are visible as such.
    source: Mapped[str] = mapped_column(String(10), default="ess")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AttendancePolicy(TenantBase):
    """One row per company; created on first read with sensible defaults.

    `timezone` is not decoration. Grouping punches by UTC date misfiles a 2am
    IST punch onto the previous day (verified), which would silently corrupt
    night-shift and early-start attendance for exactly the Indian SMEs this
    product targets.
    """

    __tablename__ = "attendance_policies"
    workday_start: Mapped[time] = mapped_column(Time, default=time(9, 30))
    expected_minutes: Mapped[int] = mapped_column(Integer, default=480)  # 8h
    grace_minutes: Mapped[int] = mapped_column(Integer, default=15)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
