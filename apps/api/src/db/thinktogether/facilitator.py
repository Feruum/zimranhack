"""Durable idempotency and expiring per-room reasoning lease.

The lease is acquired with database CAS, so multiple API replicas cannot run
different turns concurrently. No transaction is held during provider I/O.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class TTFacilitatorState(SQLModel, table=True):
    __tablename__ = 'tt_facilitator_state'
    room_id: int = Field(foreign_key='tt_room.id', primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    lease_token: Optional[str] = None
    lease_until: float = 0


class TTFacilitationTurn(SQLModel, table=True):
    __tablename__ = 'tt_facilitation_turn'
    __table_args__ = (UniqueConstraint('org_id', 'room_id', 'utterance_id', name='uq_tt_facilitation_turn'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    room_id: int = Field(foreign_key='tt_room.id', index=True)
    utterance_id: str
    message_id: str
    model_version: str
    prompt_version: str
    action: str
    text: str = ''
    revision: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False))
