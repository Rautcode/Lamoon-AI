"""calendar assignment — one company may have many calendars

Revision ID: 0019_calendar_assignment
Revises: 0018_artifacts

Holidays were unique per `(company, day)`, which meant a company had exactly
one calendar. A Mumbai establishment and a Bengaluru one could not differ.
Working days are the denominator of salary proration, leave billing and the
overtime hourly rate, so that was **wrong pay** — quietly, for everybody at one
location, every month.

After this migration every existing tenant behaves **identically**: one
calendar, assigned at company scope from an early date, holding exactly the
holidays it had before. Nothing is destroyed and nothing changes until somebody
creates a second calendar.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0019_calendar_assignment"
down_revision = "0018_artifacts"
branch_labels = None
depends_on = None

#: Early enough that any historical period resolves to the back-filled
#: assignment. Matches compensation's EPOCH for the same reason.
EPOCH = "2000-01-01"


def upgrade() -> None:
    op.add_column(
        "work_calendars",
        sa.Column("name", sa.String(120), nullable=False,
                  server_default="Company calendar"),
    )
    op.add_column(
        "holidays", sa.Column("calendar_id", UUID(as_uuid=True), nullable=True)
    )

    op.create_table(
        "calendar_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("calendar_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_calendars.id"), nullable=False, index=True),
        sa.Column("scope_type", sa.String(20), nullable=False, server_default="company"),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_calendar_assignment_dates",
        ),
    )
    op.create_index(
        "ix_calendar_assignment_lookup", "calendar_assignments",
        ["company_id", "scope_type", "scope_id"],
    )

    # --- back-fill ----------------------------------------------------------
    #
    # RLS is FORCEd and migrations run as a non-superuser, so an unguarded
    # UPDATE ... FROM matches ZERO rows and reports success (0011 and 0012
    # learned this the hard way). Suspend it on every table touched, INCLUDING
    # the read side of each join.
    touched = ("work_calendars", "holidays", "calendar_assignments")
    for t in touched:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")

    # A company can own holidays without ever having read its calendar into
    # existence (get_calendar creates lazily). Give those a calendar first, or
    # their holidays would be orphaned by the NOT NULL below.
    #
    # Deliberately NOT filtered on deleted_at: the NOT NULL applies to every
    # row, including soft-deleted holidays, so a company whose holidays have
    # all been removed still needs a calendar for them to point at.
    op.execute("""
        INSERT INTO work_calendars (id, company_id, name, working_days,
                                    created_at, updated_at)
        SELECT gen_random_uuid(), h.company_id, 'Company calendar', '1111100',
               now(), now()
          FROM (SELECT DISTINCT company_id FROM holidays) h
         WHERE NOT EXISTS (
             SELECT 1 FROM work_calendars c
              WHERE c.company_id = h.company_id AND c.deleted_at IS NULL
         )
    """)

    # Every existing holiday belongs to its company's calendar. If a company
    # somehow has two, take the oldest — deterministic, and there is no basis
    # to prefer another.
    op.execute("""
        UPDATE holidays h
           SET calendar_id = (
               SELECT c.id FROM work_calendars c
                WHERE c.company_id = h.company_id AND c.deleted_at IS NULL
                ORDER BY c.created_at, c.id
                LIMIT 1
           )
         WHERE h.calendar_id IS NULL
    """)

    # One company-scope assignment per calendar, from EPOCH, so every
    # historical period resolves exactly what it resolved before.
    op.execute(f"""
        INSERT INTO calendar_assignments
            (id, company_id, calendar_id, scope_type, scope_id,
             effective_from, effective_to, created_at, updated_at)
        SELECT gen_random_uuid(), c.company_id, c.id, 'company', NULL,
               DATE '{EPOCH}', NULL, now(), now()
          FROM work_calendars c
         WHERE c.deleted_at IS NULL
    """)

    # Prove it moved data. A silently empty migration passes and only surfaces
    # later as everybody's working days dropping to zero.
    conn = op.get_bind()
    # EVERY row, not just live ones — the NOT NULL below does not care about
    # deleted_at, and checking only live rows is what made the first attempt
    # of this migration fail at the ALTER rather than at the assertion.
    orphaned = conn.execute(sa.text(
        "SELECT count(*) FROM holidays WHERE calendar_id IS NULL"
    )).scalar_one()
    assert orphaned == 0, f"{orphaned} holidays have no calendar — RLS may have hidden rows"

    calendars = conn.execute(sa.text(
        "SELECT count(*) FROM work_calendars WHERE deleted_at IS NULL"
    )).scalar_one()
    assignments = conn.execute(sa.text(
        "SELECT count(*) FROM calendar_assignments"
    )).scalar_one()
    assert assignments == calendars, (
        f"{assignments} assignments for {calendars} calendars — every calendar "
        "needs one or its company resolves nothing and is paid for zero days"
    )

    for t in touched:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")

    op.execute(
        "CREATE POLICY tenant_isolation ON calendar_assignments "
        "USING (company_id = current_setting('app.company_id', true)::uuid) "
        "WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
    )

    # Soft-deleted holidays keep their calendar_id, so scope the NOT NULL to
    # what the application actually reads.
    op.alter_column("holidays", "calendar_id", nullable=False)
    op.create_foreign_key(
        "fk_holiday_calendar", "holidays", "work_calendars", ["calendar_id"], ["id"]
    )

    # The uniqueness that encoded the bug: one holiday per COMPANY per day.
    op.drop_index("uq_holiday_company_day", table_name="holidays")
    op.create_index(
        "uq_holiday_calendar_day", "holidays", ["calendar_id", "day"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_holiday_calendar", "holidays", ["calendar_id"])


def downgrade() -> None:
    op.drop_index("uq_holiday_calendar_day", table_name="holidays")
    op.create_index(
        "uq_holiday_company_day", "holidays", ["company_id", "day"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_constraint("fk_holiday_calendar", "holidays", type_="foreignkey")
    op.drop_column("holidays", "calendar_id")
    op.drop_column("work_calendars", "name")
    op.execute("DROP TABLE IF EXISTS calendar_assignments CASCADE")
