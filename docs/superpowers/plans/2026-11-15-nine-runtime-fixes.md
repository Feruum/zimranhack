# Nine runtime fixes and 8096 verification

> **For agentic workers:** Use subagent-driven-development and dispatching-parallel-agents for independent domains. Track each red/green check below. The user has requested implementation and complete revalidation, so continue through deployment and verification in this session.

**Goal:** Repair all nine findings in `.learnhouse/keyflows/KEY_FLOWS_RU.md` and repeat the actual key journeys on port 8096.

**Architecture:** Keep the existing API, PostgreSQL, Redis/Celery, Hocuspocus and Next.js architecture. Fix the failing boundaries with focused regressions. Deploy serially to the existing `feat/thinktogether` worktree; preserve unrelated dirty changes and live records. Add clearly labelled synthetic QA records for the rerun, without claiming they satisfy real release gates.

**Tech Stack:** Python/SQLModel/asyncpg/Alembic/pytest, TypeScript/Yjs/Bun, Next.js/React, Docker Compose, CUA browser verification.

## 1. UTC writes and migration

Files: `apps/api/src/db/thinktogether/core.py`, new `apps/api/migrations/versions/tt_20261115_03_utc_core.py`, PostgreSQL regressions in `apps/api/src/tests/thinktogether/`.

- [x] Red: write aware UTC rows for staff grant, audit event and notification to real PostgreSQL; old schema/writers must fail.
- [x] Change all three created_at fields to `Field(default_factory=_now, sa_type=DateTime(timezone=True))`.
- [x] Add reversible migration converting existing naive values with `created_at AT TIME ZONE 'UTC'`; preserve instants and indexes.
- [x] Green: inserts, support create/reply/resolve, notifications, migration upgrade/downgrade/re-upgrade and metadata parity pass.

## 2. SSE authorization after rollback

Files: `apps/api/src/security/org_auth.py`, room/generation stream regression tests; adjust routers only if required.

- [x] Red: fetch membership, rollback, call real context again and keep a real room stream open beyond multiple polling cycles; cached expired ORM row must fail before the fix.
- [x] Invalidate/reload expired membership entries rather than performing implicit async ORM IO; preserve current membership/role checks on each polling transaction.
- [x] Green: stream remains open, emits later chat/phase events, replays from Last-Event-ID, and revokes access after membership removal.

## 3. Reliable board persistence

Files: `apps/collab/src/index.ts`, focused persistence helper and tests if extraction is needed.

- [x] Red: HTTP 503/timeout must retain the latest dirty snapshot and schedule another attempt. Include a new edit arriving during a pending PUT.
- [x] Keep pending state until acknowledged, serialize writes per board, retry with bounded delay and ensure stale completions cannot clear newer snapshots. Keep agreed-note persistence consistent.
- [x] Green: successful retry writes the newest state, no concurrent older write can overwrite it, cleanup does not strand dirty state.

## 4. Private feedback

Files: `apps/api/src/services/thinktogether/learning_evidence.py`, room/cognitive/mock privacy tests.

- [x] Red: private room attempt returns neither score nor evaluation until solution_available permits it.
- [x] Apply the same solution visibility decision to score/evaluation, while preserving authorized expert review of cognitive responses.
- [x] Green: individual/transfer, mock, cognitive and discussion/summary cases preserve their intended boundaries.

## 5. Logout at the timestamp boundary

Files: `apps/api/src/security/auth.py`, relevant router/refresh checks, security tests.

- [x] Red: token issued earlier in the same second is rejected after logout; a new login later in that second remains valid; legacy integer iat tokens remain safely handled.
- [x] Preserve sufficient issuance precision or introduce equivalent session generation semantics. Keep compatibility with existing token decoding and password-change checks.
- [x] Green: replay access and refresh fail after logout, normal login/rotation and legitimate concurrent refresh still work.

## 6. Embedded board tools

Files: `apps/web/styles/globals.css`, board toolbar/room embedding and focused web tests.

- [x] Red: room iframe at 687px must expose usable drawing/note controls rather than `display:none` with no replacement.
- [x] Provide a compact usable toolbar in embedded/mobile mode while preserving normal board controls.
- [x] Green: verify the failing 687px embedded viewport in the rendered app using mouse drawing and notes; restore temporary viewport overrides. Separate touchscreen drawing is outside these nine fixes.

## 7. Fenced CSV literals

Files: `apps/api/src/services/thinktogether/course_blocks.py`, course demo tests.

- [x] Red: a literal `{{csv:example_only}}` inside fenced code survives compilation while a real exercise marker binds exactly once.
- [x] Substitute only directive exercise bodies, preserving fenced literal text and validating real unresolved markers.
- [x] Green: duplicate/missing/unknown markers still fail; valid fences and CRLF input preserve content.

## 8. Lesson completion visibility

Files: `apps/api/src/services/thinktogether/learning_curriculum.py`, curriculum tests; web refresh only if needed.

- [x] Red: complete a lesson, reopen list/detail as its learner, and assert `completed_at` is present; another learner remains incomplete.
- [x] Join/filter the requesting learner's progress into the list and detail response.
- [x] Green: completion remains idempotent and persists across reload without generating independent mastery.

## 9. Exercise marker rendering

Files: `apps/web/components/ThinkTogether/CourseMarkdown.tsx`, block renderer tests.

- [x] Red: marker-only compiled exercise renders a working exercise without displaying `{{item:...}}`.
- [x] Use the existing binding result even when its display text is an empty string.
- [x] Green: marker-only and prose-plus-marker cases render correctly; invalid/multiple bindings stay guarded.

## Integration, deployment and full rerun

- [x] Review each domain against these requirements, then review code quality and interactions.
- [x] Run the ThinkTogether API regression suite, focused auth tests, PostgreSQL integration/migration checks, collab tests, web tests/typecheck/lint. Run serially to avoid the previous Docker overload.
- [x] Back up the existing database before schema upgrade. Build API, collab and web serially with isolated build caches, deploy to 8096, verify running hashes/images and health. Keep a concrete rollback reference.
- [x] Create uniquely named synthetic records on 8096 without touching existing learner records. Reuse the proven journey scripts with strict environment/fixture guards and fresh identifiers.
- [x] Repeat auth, profile, diagnostics/review, plan, lesson, QuestPath/hints, Retell/ThinkFirst/review/transfer, timed mock/reload, groups/chat/calendar, room phases/SSE/replay/ACL, board sync/persistence/retry, support/staff/notifications, access boundaries, authoring/publication boundaries.
- [x] Run one new synthetic AI QA draft through the real configured provider to completion; confirm job, events, validated content and durable preview. Do not publish it.
- [x] Verify provider gates honestly: no production readiness claim for unconfigured Polar/mail/LiveKit or absent real approved content.
- [x] Save before/after evidence, screenshots and final matrix; leave 8096 running. Retain all unrelated source edits and actual user data.

