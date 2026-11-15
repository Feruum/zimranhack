"""Persistent, organization-scoped ThinkTogether cohort and room records."""
from datetime import date, datetime, timezone
from uuid import uuid4
from typing import Optional

from sqlalchemy import Column, DateTime, JSON, UniqueConstraint, CheckConstraint
from sqlmodel import SQLModel, Field


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class TTCohort(SQLModel, table=True):
    __tablename__ = 'tt_cohort'
    __table_args__ = (CheckConstraint('capacity >= 4 AND capacity <= 6', name='tt_cohort_capacity'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    cohort_uuid: str = Field(default_factory=uid, unique=True, index=True)
    program_id: Optional[int] = Field(default=None, index=True)
    title: str
    language: str
    timezone: str
    schedule: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    capacity: int = 6
    status: str = Field(default='forming', index=True)
    exam_window_start: Optional[date] = None
    exam_window_end: Optional[date] = None
    created_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTCohortMember(SQLModel, table=True):
    __tablename__ = 'tt_cohort_member'
    __table_args__ = (UniqueConstraint('org_id', 'cohort_id', 'user_id', name='uq_tt_cohort_member'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    cohort_id: int = Field(foreign_key='tt_cohort.id', index=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    status: str = Field(default='invited', index=True)
    invited_by: Optional[int] = Field(default=None, foreign_key='user.id')
    created_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTRoom(SQLModel, table=True):
    __tablename__ = 'tt_room'
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    room_uuid: str = Field(default_factory=uid, unique=True, index=True)
    cohort_id: int = Field(foreign_key='tt_cohort.id', index=True)
    title: str
    case_item_id: Optional[int] = Field(default=None, foreign_key='tt_item.id', index=True)
    approved_board_summary: str = ''
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    duration_minutes: int = 90
    phase: str = 'preparation'
    revision: int = 0
    board_uuid: Optional[str] = Field(default=None, index=True, unique=True)
    roles: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    agent_enabled: bool = True
    media_enforcement_pending: bool = False
    created_by: int = Field(foreign_key='user.id')
    created_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTRoomEvent(SQLModel, table=True):
    __tablename__ = 'tt_room_event'
    __table_args__ = (UniqueConstraint('org_id', 'room_id', 'sequence', name='uq_tt_room_event_sequence'), UniqueConstraint('org_id', 'room_id', 'message_id', name='uq_tt_room_event_message'))
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    room_id: int = Field(foreign_key='tt_room.id', index=True)
    sequence: int
    kind: str
    message_id: Optional[str] = None
    actor_id: Optional[int] = Field(default=None, foreign_key='user.id')
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTRoomCommand(SQLModel, table=True):
    __tablename__ = 'tt_room_command'
    __table_args__ = (UniqueConstraint('org_id', 'room_id', 'command_id', name='uq_tt_room_command'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    room_id: int = Field(foreign_key='tt_room.id', index=True)
    actor_id: int = Field(foreign_key='user.id')
    command_id: str
    fingerprint: str
    response: dict = Field(sa_column=Column(JSON, nullable=False))


class TTRoomConsent(SQLModel, table=True):
    __tablename__ = 'tt_room_consent'
    __table_args__ = (UniqueConstraint('org_id', 'room_id', 'user_id', name='uq_tt_room_consent'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    room_id: int = Field(foreign_key='tt_room.id', index=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    audio_processing: bool = False
    consent_version: str = 'audio-v1'
    updated_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTVoiceUtterance(SQLModel, table=True):
    __tablename__ = 'tt_voice_utterance'
    __table_args__ = (UniqueConstraint('org_id', 'room_id', 'utterance_id', name='uq_tt_voice_utterance'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    room_id: int = Field(foreign_key='tt_room.id', index=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    utterance_id: str
    text: str
    created_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))


TTCohortMembership = TTCohortMember
