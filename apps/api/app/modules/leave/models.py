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
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase


class LeaveType(TenantBase):
    __tablename__ = "leave_types"
    name: Mapped[str] = mapped_column(String(100))
    annual_quota: Mapped[int] = mapped_column(Integer)  # days/year, same for every employee in V1
    #: Unpaid leave becomes loss of pay in the payroll run. Defaults True so
    #: adding this flag can't retroactively dock anyone for leave already taken.
    paid: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Compensatory off is EARNED by working a holiday or weekly off, never
    #: granted by a policy — so it belongs to the type, not to an entitlement
    #: rule. A company either runs comp-off or it does not.
    comp_off: Mapped[bool] = mapped_column(Boolean, default=False)


class LeaveRequest(TenantBase):
    __tablename__ = "leave_requests"
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"))
    leave_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leave_types.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    #: Server-computed working days, in halves. A half-day of unpaid leave is
    #: half a day of loss of pay, so this cannot be an integer without payroll
    #: quietly rounding somebody's deduction.
    days: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
