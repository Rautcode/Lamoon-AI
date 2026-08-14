"""Which leave policy applies to THIS employee, for THIS leave type.

The same shape as calendar assignment (C1), deliberately: a scoped,
effective-dated rule with inheritance, resolved by a pure function. Two
different answers to "which rule applies to whom" would be two different sets
of bugs.

`LeaveType.annual_quota` is not deleted. It becomes the fallback for a leave
type nobody has written a policy for, so an existing tenant keeps working
unchanged until somebody writes one.

**Scoping is limited by what an employee actually has today.** The plan calls
for policy per grade / location / employment type; none of those columns exist
until B1. What does exist — establishment, department, worker_type — is
genuinely useful (a factory and a head office differ, and blue-collar leave is
a different animal), and adding `grade` later is a row in SCOPE_PRECEDENCE, not
a migration.
"""
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase

#: Most specific wins, as in C1. `grade` is listed but unassignable — the
#: column arrives with B1, and listing it now keeps the ordering honest.
SCOPE_PRECEDENCE = ("company", "establishment", "department", "worker_type", "grade")


class LeavePolicy(TenantBase):
    """One leave type's rules, for one scope, over one span of time."""

    __tablename__ = "leave_policies"
    __table_args__ = (
        Index(
            "ix_leave_policy_lookup", "company_id", "leave_type_id", "scope_type",
        ),
    )

    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leave_types.id"), index=True
    )

    scope_type: Mapped[str] = mapped_column(String(20), default="company")
    #: NULL for company scope, and for worker_type the value lives in
    #: `scope_value` because a worker type is a word, not a row.
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope_value: Mapped[str | None] = mapped_column(String(40), nullable=True)

    #: Days per leave year, before any proration.
    annual_days: Mapped[float] = mapped_column(Numeric(5, 1), default=0)
    #: annual | monthly — see entitlement.ACCRUAL_METHODS.
    accrual_method: Mapped[str] = mapped_column(String(10), default="annual")
    #: Whether a mid-year joiner or leaver gets a proportion. Off means the
    #: full quota regardless, which some companies genuinely do for casual
    #: leave and which must therefore be expressible rather than assumed.
    prorate_on_joining: Mapped[bool] = mapped_column(default=True)
    prorate_on_exit: Mapped[bool] = mapped_column(default=True)

    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


@dataclass(frozen=True)
class Applicable:
    """A resolved policy, flattened, with the scope that produced it."""

    annual_days: str
    accrual_method: str
    prorate_on_joining: bool
    prorate_on_exit: bool
    source: str


@dataclass(frozen=True)
class Candidate:
    """One policy row, as pure data, so precedence is testable without a DB."""

    scope_type: str
    scope_id: uuid.UUID | None
    scope_value: str | None
    annual_days: str
    accrual_method: str
    prorate_on_joining: bool
    prorate_on_exit: bool
    effective_from: date
    effective_to: date | None

    def covers(self, on: date) -> bool:
        return self.effective_from <= on and (
            self.effective_to is None or on <= self.effective_to
        )


def pick_policy(
    candidates: list[Candidate],
    *,
    establishment_id: uuid.UUID | None,
    department_id: uuid.UUID | None,
    worker_type: str | None,
    on: date,
) -> Applicable | None:
    """The policy in force for this employee on this date.

    Specificity beats recency, exactly as in calendar resolution: a
    company-wide policy written in July must not take over a department that
    has had its own since 2020.

    Returns None when nothing matches, so the caller can fall back to the leave
    type's own quota rather than silently granting zero days — which is the
    difference between "no policy" and "no leave".
    """
    matches = [
        c
        for c in candidates
        if c.covers(on)
        and (
            c.scope_type == "company"
            or (c.scope_type == "establishment" and c.scope_id == establishment_id)
            or (c.scope_type == "department" and c.scope_id == department_id)
            or (c.scope_type == "worker_type" and c.scope_value == worker_type)
        )
    ]
    if not matches:
        return None
    best = max(
        matches,
        key=lambda c: (SCOPE_PRECEDENCE.index(c.scope_type), c.effective_from),
    )
    return Applicable(
        annual_days=best.annual_days,
        accrual_method=best.accrual_method,
        prorate_on_joining=best.prorate_on_joining,
        prorate_on_exit=best.prorate_on_exit,
        source=best.scope_type,
    )
