"""Persistent cohort discussion, independent of scheduled room chat."""
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel
from src.services.thinktogether.ids import public_id, utcnow


class TTCohortMessage(SQLModel, table=True):
    __tablename__ = 'tt_cohort_message'
    __table_args__ = (
        UniqueConstraint('org_id', 'cohort_id', 'user_id', 'idempotency_key', name='uq_tt_cohort_message_key'),
        Index('ix_tt_cohort_message_cursor', 'org_id', 'cohort_id', 'id'),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    message_uuid: str = Field(default_factory=public_id, unique=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    cohort_id: int = Field(foreign_key='tt_cohort.id', index=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    idempotency_key: str = Field(max_length=128)
    fingerprint: str = Field(max_length=64)
    text: str = Field(max_length=4000)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
