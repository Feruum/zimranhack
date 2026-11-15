# ThinkTogether implementation contract

Approved product: full PMP preparation, mandatory goals/domain/cognitive diagnostics,
real cohorts of 4–6, independent attempts before/after group work, delayed checks,
text/board/video and a multi-participant voice facilitator. AGPL project; Polar payments;
LiveKit Cloud; existing ThinkTogether auth/content/LLM adapters. No private EE dependency.

Implementation and operational evidence are recorded separately in
[STATUS.md](STATUS.md) and [RUNBOOK.md](RUNBOOK.md). The source currently declares
42 ThinkTogether tables; its target Alembic head is `tt_20261115_02`. This is a
schema target, not evidence of a completed restore or permission to launch.

## Shared boundaries

- API prefix `/api/v1/tt`. Root mounts routers; workers must not edit `src/router.py`.
- Domain models live in `src/db/thinktogether/{learning,rooms,billing,jobs}.py`.
- Services live in `src/services/thinktogether/`; routers in `src/routers/thinktogether/`.
- Every table has `__tablename__ = "tt_..."`, `org_id`, and integer internal IDs where appropriate.
- Public identifiers use UUID strings, timestamps UTC timezone-aware datetimes; JSON columns use portable SQLAlchemy JSON.
- Existing `user.id`, `organization.id` remain the identity roots. Never trust client org/user IDs.
- Root owns `src/services/thinktogether/common.py`: `TTContext(user, org_id, is_staff, staff_roles)`,
  `get_context`, `require_staff`, `require_permission(ctx, permission)`, `utcnow`, `public_id`.
  `ctx.user.id` identifies caller; `ctx.org_id` is configured TT organization; `ctx.is_staff`
  means admin/maintainer, expert, operator or psychometric specialist. `ctx.can(permission)`
  accepts `content`, `groups`, `support`, `psychometrics`, `billing`; admins pass all.
- HTTP handlers use `ctx: TTContext = Depends(get_context)` and `db: AsyncSession = Depends(get_db_session)`.
- Parent mounts each domain router; paths in domain router are relative (no `/tt` prefix).
- Do not implement an in-memory database, fake payments, fake AI grades or fake cognitive norms.
- Versioned draft authoring may include original demonstration items, clearly not an approved full curriculum.

## Ownership

- learning worker: learning models/services/routes/tests, program/content publication, profiles,
  diagnostics, immutable attempts, assessments, evidence, adaptive plans, expert review.
- rooms worker: rooms models/services/routes/tests; groups, membership, schedules, room phases,
  durable chat/events, LiveKit media tokens, boards linkage, collab phase ACL.
- billing worker: billing/jobs models/services/routes/tests; Polar, reservations, entitlements,
  webhook inbox, outbox, Celery worker/scheduler, requirements extras for these only.
- parent: common/auth, router mounting, migrations, source compatibility, deployment, final integration.

## Cross-domain contracts

- Learning exports `learning_readiness(db, org_id, user_id) -> dict` with booleans
  `profile_complete`, `domain_diagnostic_complete`, `cognitive_complete`, `ready`.
- Rooms exports `require_cohort_member(db, org_id, user_id, cohort_id)` and
  `require_room_member(db, org_id, user_id, room_id)`; staff authorization separately checked.
- Billing exports `has_active_access(db, org_id, user_id, program_id=None) -> bool` and
  `require_active_access(db, org_id, user_id, program_id=None)` (HTTP 403 on denial).
- Domain jobs can be queued via jobs service `enqueue_job(db, org_id, kind, payload, key)`
  within caller's transaction; it must not commit the caller's transaction.
- Seat reservations and group allocation are billing-owned, with imports of rooms models.
  Coordinate exported room model class/field names promptly.
- No chat/board/voice input is independent mastery evidence. Hint use and exposed case
  families disqualify an attempt from independent evidence.
- `practice_reserved_families` and `validate_program_practice_bank` in
  `learning_content.py` share the reservation rules used by live selection and
  publication. Draft instruments do not reserve live cases. Publishing an
  instrument must preserve published courses' independent banks, including
  family aliases and replacement versions.
- Cognitive assessment responses remain private. Closed items use approved
  deterministic keys; open cognitive reasoning requires a psychometric specialist.
  Generic `learning.grade` jobs never send those responses to the educational
  LLM, including jobs queued before this restriction. Ordinary PMP grading keeps
  its separate configured-provider path and content-free usage accounting.

## Delivery gates

Real launch requires approved original/licensed content, an expert-approved licensed
cognitive instrument, real compatible cohorts, approved merchant account and external
service credentials. Software must expose these as readiness gates and fail closed.
Support locally runnable tests and seed original sample content without making these claims.

Cognitive instrument publication also requires an expert-authored, versioned
support policy with explicit adaptive-use rights. Its bounded criterion rules
provide initial study recommendations; later independent performance overrides
them. Neither the policy nor software supplies intelligence labels or invented norms.

## Verification

Use pytest against real SQLModel persistence (SQLite unit tests, PostgreSQL integration
for transactions/locks), existing authenticated users, and authorization-negative tests.
Never print `.env`, tokens, provider credentials, PM2 JSON or full model environments.
Do not commit; parent coordinates the final repository state. Other workers share this
worktree: do not revert, overwrite or broadly format their changes.
