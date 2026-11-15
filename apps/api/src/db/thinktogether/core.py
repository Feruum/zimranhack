from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TTStaffGrant(SQLModel, table=True):
    __tablename__ = 'tt_staff_grant'
    __table_args__ = (UniqueConstraint('org_id', 'user_id', 'role', name='uq_tt_staff_role'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    role: str = Field(max_length=32)
    created_at: datetime = Field(default_factory=_now, sa_type=DateTime(timezone=True))


class TTAuditEvent(SQLModel, table=True):
    __tablename__ = 'tt_audit_event'
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    actor_user_id: Optional[int] = Field(default=None, foreign_key='user.id')
    action: str = Field(index=True, max_length=100)
    resource_id: str = Field(default='', max_length=100)
    details: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=_now, sa_type=DateTime(timezone=True), index=True)


class TTResourceGrant(SQLModel, table=True):
    """Map a legacy course/media resource to a paid ThinkTogether program."""
    __tablename__ = 'tt_resource_grant'
    __table_args__ = (UniqueConstraint('org_id', 'resource_uuid', name='uq_tt_resource_grant'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    program_id: int = Field(foreign_key='tt_program.id', index=True)
    resource_uuid: str = Field(index=True, max_length=100)


class TTNotification(SQLModel, table=True):
    __tablename__ = 'tt_notification'
    __table_args__ = (UniqueConstraint('org_id', 'user_id', 'dedup_key', name='uq_tt_notification'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    dedup_key: str
    title: str
    href: str
    read: bool = False
    emailed: bool = False
    created_at: datetime = Field(default_factory=_now, sa_type=DateTime(timezone=True), index=True)


class TTNotificationPreference(SQLModel, table=True):
    __tablename__ = 'tt_notification_preference'
    __table_args__ = (UniqueConstraint('org_id', 'user_id', name='uq_tt_notification_preference'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key='organization.id', index=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    email_reminders: bool = False
