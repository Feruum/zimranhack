"""PostgreSQL is the durable queue; Redis/Celery is only the delivery transport."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel

from src.services.thinktogether.ids import utcnow, public_id


class TTJob(SQLModel, table=True):
    __tablename__ = "tt_job"
    __table_args__ = (UniqueConstraint("org_id", "idempotency_key", name="uq_tt_job_key"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    job_uuid: str = Field(default_factory=public_id, unique=True)
    org_id: int = Field(index=True)
    kind: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_type=JSON)
    idempotency_key: str
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    max_attempts: int = 8
    available_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True), index=True)
    lease_until: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
    lease_token: Optional[str] = None
    last_error: Optional[str] = None
    dispatched_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    completed_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))


class TTSchedulerLease(SQLModel, table=True):
    __tablename__ = "tt_scheduler_lease"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(default=0, index=True)
    name: str = Field(unique=True)
    owner: str
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
