"""phase17_backfill_person_company_role_start_date

Revision ID: 3d9d243b9c84
Revises: bac667b20089
Create Date: 2026-09-14 11:11:27.102927

Data-only migration, no schema change. Backfills PersonCompanyRole.start_date
(added in an earlier phase but never populated) for every existing row with
start_date IS NULL, using the earliest Event.publication_time among that
(person, company) pair's InsiderTransaction rows — the same "earliest Form 4
disclosure" rule capint.ingestion.sec_form4.upsert_person_company_role now
applies going forward for every newly-ingested role. Done row-by-row in
Python (not a single correlated-subquery UPDATE) to avoid relying on a
date-truncation SQL function that behaves differently across SQLite and
Postgres — this project's scale doesn't make that a performance concern.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d9d243b9c84'
down_revision: Union[str, Sequence[str], None] = 'bac667b20089'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill start_date for existing PersonCompanyRole rows."""
    bind = op.get_bind()
    metadata = sa.MetaData()
    person_company_roles = sa.Table("person_company_roles", metadata, autoload_with=bind)
    insider_transactions = sa.Table("insider_transactions", metadata, autoload_with=bind)
    events = sa.Table("events", metadata, autoload_with=bind)

    roles = bind.execute(
        sa.select(
            person_company_roles.c.id,
            person_company_roles.c.person_entity_id,
            person_company_roles.c.company_entity_id,
        ).where(person_company_roles.c.start_date.is_(None))
    ).fetchall()

    for role_id, person_entity_id, company_entity_id in roles:
        earliest_publication = bind.execute(
            sa.select(sa.func.min(events.c.publication_time))
            .select_from(insider_transactions.join(events, events.c.id == insider_transactions.c.event_id))
            .where(
                insider_transactions.c.insider_entity_id == person_entity_id,
                insider_transactions.c.company_entity_id == company_entity_id,
            )
        ).scalar()
        if earliest_publication is not None:
            bind.execute(
                person_company_roles.update()
                .where(person_company_roles.c.id == role_id)
                .values(start_date=earliest_publication.date())
            )


def downgrade() -> None:
    """Revert every start_date this migration could have set. Safe because
    upgrade() only ever writes a value where start_date was NULL — nothing
    else in this system's migration history populates this column."""
    bind = op.get_bind()
    metadata = sa.MetaData()
    person_company_roles = sa.Table("person_company_roles", metadata, autoload_with=bind)
    bind.execute(person_company_roles.update().values(start_date=None))
