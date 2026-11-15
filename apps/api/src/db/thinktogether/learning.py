"""Persistent, organization-scoped ThinkTogether learning records.

Content and instruments are version rows. Attempts, evaluations and consent are
append-only; mastery and plans are projections that can be rebuilt from evidence.
"""
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, JSON, UniqueConstraint, event
from sqlmodel import Field, SQLModel


def now_utc():
    return datetime.now(timezone.utc)


class LearningRow(SQLModel):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=lambda: str(uuid4()), unique=True, index=True)
    org_id: int = Field(foreign_key="organization.id", index=True)
    created_at: datetime = Field(default_factory=now_utc, sa_type=DateTime(timezone=True), nullable=False)


class TTProgram(LearningRow, table=True):
    __tablename__ = "tt_program"
    __table_args__ = (UniqueConstraint("org_id", "series_id", "version"),)
    series_id: str = Field(default_factory=lambda: str(uuid4()), index=True)
    version: int = 1
    title: str
    description: str = ""
    status: str = "draft"
    is_sample: bool = True
    full_curriculum: bool = False
    blueprint_version: str = ""
    approval_evidence: str = ""
    approved_by: Optional[int] = Field(default=None, foreign_key="user.id")
    published_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    curriculum: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class TTSkill(LearningRow, table=True):
    __tablename__ = "tt_skill"
    __table_args__ = (UniqueConstraint("program_id", "code"),)
    program_id: int = Field(foreign_key="tt_program.id", index=True)
    code: str
    title: str
    description: str = ""
    domain: str = ""
    prerequisites: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    estimated_minutes: int = 5
    position: int = 0


class TTItem(LearningRow, table=True):
    __tablename__ = "tt_item"
    __table_args__ = (UniqueConstraint("org_id", "series_id", "version"),)
    program_id: int = Field(foreign_key="tt_program.id", index=True)
    skill_id: int = Field(foreign_key="tt_skill.id", index=True)
    series_id: str = Field(default_factory=lambda: str(uuid4()), index=True)
    version: int = 1
    family_id: str = Field(index=True)
    title: str
    prompt: str
    kind: str = "single_choice"
    choices: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    answer_key: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    rubric: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    explanation: str = ""
    hints: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    estimated_minutes: int = 5
    difficulty: int = 2
    status: str = "draft"
    is_sample: bool = True
    approval_evidence: str = ""
    approved_by: Optional[int] = Field(default=None, foreign_key="user.id")


class TTInstrument(LearningRow, table=True):
    __tablename__ = "tt_instrument"
    __table_args__ = (UniqueConstraint("org_id", "series_id", "version"),)
    series_id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 1
    title: str
    kind: str = "cognitive"
    status: str = "draft"
    is_sample: bool = True
    license_reference: str = ""
    approval_evidence: str = ""
    approved_by: Optional[int] = Field(default=None, foreign_key="user.id")
    consent_version: str = "1"
    consent_text: str = ""
    definition: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    # Contains item IDs, dimension mappings and published scoring rules; never
    # sent to learners. No inferred population percentiles or intelligence labels.


class TTLearnerProfile(LearningRow, table=True):
    __tablename__ = "tt_learner_profile"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    user_id: int = Field(foreign_key="user.id", index=True)
    program_id: Optional[int] = Field(default=None, foreign_key="tt_program.id")
    goal: str = ""
    exam_date: str = ""
    minutes_per_day: int = 30
    timezone: str = "UTC"
    language: str = "en"
    schedule_slots: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    domain_profile: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    cognitive_profile: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=now_utc, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTConsent(LearningRow, table=True):
    __tablename__ = "tt_consent"
    user_id: int = Field(foreign_key="user.id", index=True)
    instrument_id: int = Field(foreign_key="tt_instrument.id", index=True)
    version: str
    accepted: bool
    text_snapshot: str


class TTDiagnosticRun(LearningRow, table=True):
    __tablename__ = "tt_diagnostic_run"
    user_id: int = Field(foreign_key="user.id", index=True)
    program_id: Optional[int] = Field(default=None, foreign_key="tt_program.id")
    instrument_id: int = Field(foreign_key="tt_instrument.id", index=True)
    kind: str
    status: str = "in_progress"
    item_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class TTDelivery(LearningRow, table=True):
    __tablename__ = "tt_delivery"
    __table_args__ = (UniqueConstraint('org_id', 'user_id', 'room_id', 'room_phase'),)
    user_id: int = Field(foreign_key="user.id", index=True)
    item_id: int = Field(foreign_key="tt_item.id")
    phase: str = "independent"
    diagnostic_id: Optional[int] = Field(default=None, foreign_key="tt_diagnostic_run.id")
    review_id: Optional[int] = Field(default=None, foreign_key="tt_review.id")
    mock_run_id: Optional[int] = Field(default=None, foreign_key="tt_mock_run.id")
    room_id: Optional[int] = Field(default=None, foreign_key="tt_room.id")
    room_phase: str = ''
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class TTExposure(LearningRow, table=True):
    __tablename__ = "tt_exposure"
    user_id: int = Field(foreign_key="user.id", index=True)
    family_id: str = Field(index=True)
    source: str  # hint, solution, group, board, voice, external
    source_id: str = ""


class TTAttempt(LearningRow, table=True):
    __tablename__ = "tt_attempt"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "idempotency_key"),
                      UniqueConstraint("delivery_id"))
    user_id: int = Field(foreign_key="user.id", index=True)
    item_id: int = Field(foreign_key="tt_item.id", index=True)
    skill_id: int = Field(foreign_key="tt_skill.id", index=True)
    family_id: str = Field(index=True)
    delivery_id: Optional[int] = Field(default=None, foreign_key="tt_delivery.id")
    diagnostic_id: Optional[int] = Field(default=None, foreign_key="tt_diagnostic_run.id")
    idempotency_key: str
    request_hash: str
    response: dict = Field(sa_column=Column(JSON, nullable=False))
    phase: str = "independent"
    assisted: bool = False
    unseen_family: bool = True
    content_approved: bool = False
    elapsed_seconds: Optional[int] = None


class TTEvaluation(LearningRow, table=True):
    __tablename__ = "tt_evaluation"
    __table_args__ = (UniqueConstraint("attempt_id", "revision"),)
    attempt_id: int = Field(foreign_key="tt_attempt.id", index=True)
    revision: int = 1
    source: str  # deterministic, ai, expert, pending
    status: str = "scored"
    score: Optional[float] = None
    feedback: str = ""
    dimensions: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    evaluator_id: Optional[int] = Field(default=None, foreign_key="user.id")
    model: str = ""


class TTMastery(LearningRow, table=True):
    __tablename__ = "tt_mastery"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "skill_id"),)
    user_id: int = Field(foreign_key="user.id", index=True)
    skill_id: int = Field(foreign_key="tt_skill.id", index=True)
    state: str = "new"
    independent_days: int = 0
    independent_families: int = 0
    evidence_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    latest_score: Optional[float] = None
    revision: int = 1
    updated_at: datetime = Field(default_factory=now_utc, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTReview(LearningRow, table=True):
    __tablename__ = "tt_review"
    __table_args__ = (UniqueConstraint("attempt_id", "interval_days"),)
    user_id: int = Field(foreign_key="user.id", index=True)
    skill_id: int = Field(foreign_key="tt_skill.id", index=True)
    attempt_id: int = Field(foreign_key="tt_attempt.id")
    interval_days: int
    due_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    status: str = "pending"
    completed_attempt_id: Optional[int] = Field(default=None, foreign_key="tt_attempt.id")


class TTPlan(LearningRow, table=True):
    __tablename__ = "tt_plan"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "plan_date", "version"),)
    user_id: int = Field(foreign_key="user.id", index=True)
    program_id: Optional[int] = Field(default=None, foreign_key="tt_program.id")
    plan_date: str
    version: int = 1
    budget_minutes: int
    total_minutes: int = 0
    status: str = "ready"
    activities: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    rationale: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    support_case_id: Optional[str] = None


class TTSupportCase(LearningRow, table=True):
    __tablename__ = "tt_support_case"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "dedup_key"),)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str
    dedup_key: str
    status: str = "open"
    details: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class TTContentAudit(LearningRow, table=True):
    __tablename__ = "tt_content_audit"
    actor_id: int = Field(foreign_key="user.id")
    resource: str
    resource_id: str
    previous_status: str
    status: str
    evidence: str = ""


class TTLesson(LearningRow, table=True):
    __tablename__ = "tt_lesson"
    __table_args__ = (UniqueConstraint("org_id", "series_id", "version"),)
    program_id: int = Field(foreign_key="tt_program.id", index=True)
    skill_id: int = Field(foreign_key="tt_skill.id", index=True)
    series_id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 1
    title: str
    body_markdown: str
    objectives: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    case_families: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    estimated_minutes: int = 10
    position: int = 0
    status: str = "draft"
    is_sample: bool = True
    approval_evidence: str = ""
    approved_by: Optional[int] = Field(default=None, foreign_key="user.id")


class TTMockForm(LearningRow, table=True):
    __tablename__ = "tt_mock_form"
    __table_args__ = (UniqueConstraint("program_id", "form_code"),)
    program_id: int = Field(foreign_key="tt_program.id", index=True)
    title: str
    form_code: str
    item_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    duration_minutes: int = 240
    status: str = "draft"
    is_sample: bool = True
    approval_evidence: str = ""
    approved_by: Optional[int] = Field(default=None, foreign_key="user.id")


class TTLessonProgress(LearningRow, table=True):
    __tablename__ = "tt_lesson_progress"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "lesson_id"),)
    user_id: int = Field(foreign_key="user.id", index=True)
    lesson_id: int = Field(foreign_key="tt_lesson.id", index=True)
    completed_at: datetime = Field(default_factory=now_utc, sa_column=Column(DateTime(timezone=True), nullable=False))


class TTMockRun(LearningRow, table=True):
    __tablename__ = "tt_mock_run"
    user_id: int = Field(foreign_key="user.id", index=True)
    form_id: int = Field(foreign_key="tt_mock_form.id")
    status: str = "in_progress"
    deadline: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    deliveries: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    attempt_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    score: Optional[float] = None
    result: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))


def _immutable(mapper, connection, target):
    raise ValueError("Evidence records are immutable; append a new revision")


for _model in (TTAttempt, TTEvaluation, TTConsent, TTExposure, TTContentAudit):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
