"""Persistent discussion between cohort sessions."""
from alembic import op
import sqlalchemy as sa

revision = 'tt_20261115_02'
down_revision = 'tt_20261114_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('tt_cohort_message',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('message_uuid', sa.String(), nullable=False),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id'), nullable=False),
        sa.Column('cohort_id', sa.Integer(), sa.ForeignKey('tt_cohort.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('text', sa.String(4000), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('message_uuid'),
        sa.UniqueConstraint('org_id', 'cohort_id', 'user_id', 'idempotency_key', name='uq_tt_cohort_message_key'))
    op.create_index('ix_tt_cohort_message_org_id', 'tt_cohort_message', ['org_id'])
    op.create_index('ix_tt_cohort_message_cohort_id', 'tt_cohort_message', ['cohort_id'])
    op.create_index('ix_tt_cohort_message_user_id', 'tt_cohort_message', ['user_id'])
    op.create_index('ix_tt_cohort_message_cursor', 'tt_cohort_message', ['org_id', 'cohort_id', 'id'])


def downgrade():
    op.drop_table('tt_cohort_message')
