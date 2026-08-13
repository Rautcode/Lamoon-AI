"""Effective-dated compensation.

The model this replaces stored one live salary per employee and soft-deleted
the old rows on a change. That preserves a *transaction* history — these rows
were replaced at 14:32 on 5 August — but not an *effective* one. A raise
entered on 5 August effective from 1 July is indistinguishable from one
effective 5 August, and payroll cannot answer the only question it actually
needs to ask:

    what compensation applied to this employee during THIS period?

So a version carries the dates it is true for, and nothing is ever overwritten.
A correction to a past version is a new version, and the old one stays readable
because a finalized payslip computed from it has to remain explicable years
later.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase
from app.modules.payroll.models import MONEY

#: Why a version exists. Not decoration — movement analysis reports "raise"
#: separately from "data fix", and only one of those is a pay rise.
REASONS = ("hire", "revision", "promotion", "correction", "migration", "f_and_f")


class CompensationVersion(TenantBase):
    """One employee's salary, and the span of time it is true for.

    `effective_to` NULL means open-ended — the current salary. Exactly one open
    version per employee at a time; the service closes the previous one when a
    new version starts.

    A real no-overlap guarantee wants an EXCLUDE constraint over
    `daterange(effective_from, effective_to)`, which needs the btree_gist
    extension. The application role is not a superuser and cannot create it, so
    non-overlap is maintained by the service (which closes the previous version
    and bounds the new one by the next) and asserted by an invariant test. A
    DBA can upgrade this to a hard constraint without a code change:

        CREATE EXTENSION btree_gist;
        ALTER TABLE compensation_versions ADD CONSTRAINT no_overlapping_versions
          EXCLUDE USING gist (employee_id WITH =,
                              daterange(effective_from, effective_to, '[]') WITH &&)
          WHERE (deleted_at IS NULL);
    """

    __tablename__ = "compensation_versions"
    __table_args__ = (
        # Two versions cannot begin on the same day. Necessary but not
        # sufficient for non-overlap — see the class docstring.
        Index(
            "uq_compensation_version_start", "employee_id", "effective_from",
            unique=True, postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_compensation_version_lookup", "employee_id", "effective_from"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_compensation_version_dates",
        ),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    #: NULL = open-ended. Closed when a later version starts.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(20), default="revision")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The version this one corrects, when it is a correction rather than a
    #: change. Lets an auditor follow "what did we think the salary was, and
    #: when did we decide otherwise".
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compensation_versions.id"), nullable=True
    )


class CompensationLine(TenantBase):
    """One component's amount within one version."""

    __tablename__ = "compensation_lines"
    __table_args__ = (
        Index(
            "uq_compensation_line", "version_id", "component_id",
            unique=True, postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compensation_versions.id"), index=True
    )
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pay_components.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
