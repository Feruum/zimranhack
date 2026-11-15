"""Store UTC-aware staff, audit and notification timestamps."""
from alembic import op
import sqlalchemy as sa

revision = 'tt_20261115_03'
down_revision = 'tt_20261115_02'
branch_labels = None
depends_on = None

TABLES = ('tt_staff_grant', 'tt_audit_event', 'tt_notification')


def upgrade():
    for table in TABLES:
        op.alter_column(table, 'created_at',
            existing_type=sa.DateTime(timezone=False),
            type_=sa.DateTime(timezone=True), existing_nullable=False,
            postgresql_using="created_at AT TIME ZONE 'UTC'")


def downgrade():
    for table in reversed(TABLES):
        op.alter_column(table, 'created_at',
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(timezone=False), existing_nullable=False,
            postgresql_using="created_at AT TIME ZONE 'UTC'")
