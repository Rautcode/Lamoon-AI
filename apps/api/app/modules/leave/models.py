"""Leave management. Both tables RLS-scoped (TenantBase).

Deliberately no `leave_balances` table: a balance is derived —
`leave_types.annual_quota - sum(days of approved requests this calendar
year)` — computed in service.py, never stored. A stored counter is one more
place to drift out of sync with the requests it's supposed to summarize; the
requests ARE the ledger.

Scope: HR/admin/manager administer this (leave.write/leave.approve). No
employee self-submission yet — that's Employee Self-Service, a separate,
already-acknowledged module this doesn't build.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase


class LeaveType(TenantBase):
    __tablename__ = "leave_types"
    name: Mapped[str] = mapped_column(String(100))
    annual_quota: Mapped[int] = mapped_column(Integer)  # days/year, same for every employee in V1


class LeaveRequest(TenantBase):
    __tablename__ = "leave_requests"
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"))
    leave_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leave_types.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)  # server-computed, inclusive calendar days
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
