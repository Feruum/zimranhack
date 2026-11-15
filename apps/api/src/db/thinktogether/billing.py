"""Payment state, seat holds, and access are separate durable facts."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel

from src.services.thinktogether.ids import public_id, utcnow


class TTProductConfig(SQLModel, table=True):
    __tablename__ = "tt_product_config"
    __table_args__ = (UniqueConstraint("org_id", "program_id", name="uq_tt_product_program"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="organization.id", index=True)
    program_id: int = Field(foreign_key="tt_program.id", index=True)
    polar_product_id: str
    price_amount: int = 14900
    currency: str = "usd"
    access_days: int = 56
    enabled: bool = False
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class TTSeatReservation(SQLModel, table=True):
    __tablename__ = "tt_seat_reservation"
    id: Optional[int] = Field(default=None, primary_key=True)
    reservation_uuid: str = Field(default_factory=public_id, unique=True)
    org_id: int = Field(foreign_key="organization.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    user_uuid: str
    program_id: int = Field(foreign_key="tt_program.id")
    cohort_id: int = Field(foreign_key="tt_cohort.id", index=True)
    product_config_id: int = Field(foreign_key="tt_product_config.id")
    polar_product_id: str = ""
    # Immutable purchase terms survive later catalog edits.
    price_amount: int = 14900
    currency: str = "usd"
    access_days: int = 56
    status: str = Field(default="held", index=True)
    polar_checkout_id: Optional[str] = Field(default=None, unique=True)
    checkout_url: Optional[str] = None
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class TTPaymentOrder(SQLModel, table=True):
    __tablename__ = "tt_payment_order"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="organization.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    program_id: int = Field(foreign_key="tt_program.id")
    reservation_id: int = Field(foreign_key="tt_seat_reservation.id", unique=True)
    polar_order_id: str = Field(unique=True)
    status: str = "pending"
    total_amount: int = 0
    refunded_amount: int = 0
    currency: str = "usd"
    refund_requested: bool = False
    polar_refund_id: Optional[str] = None
    checked_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True), index=True)
    paid_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))


class TTEntitlement(SQLModel, table=True):
    __tablename__ = "tt_entitlement"
    id: Optional[int] = Field(default=None, primary_key=True)
    entitlement_uuid: str = Field(default_factory=public_id, unique=True)
    org_id: int = Field(foreign_key="organization.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    program_id: int = Field(foreign_key="tt_program.id", index=True)
    order_id: Optional[int] = Field(default=None, foreign_key="tt_payment_order.id", unique=True)
    source: str = "polar"
    granted_by: Optional[int] = Field(default=None, foreign_key="user.id")
    grant_reason: Optional[str] = None
    status: str = Field(default="active", index=True)
    starts_at: datetime = Field(sa_type=DateTime(timezone=True))
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
    revoked_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))


class TTWebhookInbox(SQLModel, table=True):
    __tablename__ = "tt_webhook_inbox"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="organization.id", index=True)
    webhook_id: str = Field(unique=True)
    event_type: str
    # Store only routing identifiers after SDK validation, never raw PII or secrets.
    payload: dict = Field(default_factory=dict, sa_type=JSON)
    status: str = "queued"
    received_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    processed_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
