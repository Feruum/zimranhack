# ThinkTogether implementation status

As of 2026-11-15. This records software scope; it does not approve a paid launch.

## Implemented

- Versioned programs, prerequisite skills, authored lessons/items, publication
  audit, package validation and import/export. Full-course gates cover the 2026
  blueprint, lesson coverage, independent practice banks and two timed held-out
  mock forms. Publishing diagnostics cannot consume a live course's minimum bank.
- Private goals, consent and approved diagnostics; specialist-only open cognitive
  review and deterministic closed scoring. Raw psychometric responses never enter
  the generic educational AI grading path, including old queued jobs.
- Expert-authored initial support policies, budgeted Today plans, independent
  attempts, immutable evaluation revisions, evidence replay and delayed reviews.
  Retell/ThinkFirst are coached; fresh-case transfer and QuestPath error recovery
  remain separate independent practice.
- Authenticated learner/staff interfaces, scoped cohorts and persistent cohort
  discussion, scheduled room phases, private tasks, chat, boards and media adapters.
- Polar checkout/reservations, verified webhook processing, entitlements,
  transactional jobs, notifications, support workflows and content-free AI usage
  accounting. Provider operations remain configuration/evidence gated.

Source declares **42 `tt_` tables**. The verified local migration head is
**`tt_20261115_02`**, whose predecessor is `tt_20261114_01`; the new revision adds
`tt_cohort_message`. Upgrade, metadata parity, downgrade and re-upgrade passed
for all 42 tables. The [runbook](RUNBOOK.md) contains migration and test commands.

## Verification limits

Final recorded local results:

- ThinkTogether API: **200 passed**, including **7 real PostgreSQL tests** and
  regressions for UTC audit writes and usage reporting on asyncpg.
- Full web suite: **743 passed**, 0 failed, 2,025 assertions across 69 files
  (`bun test --isolate --timeout 20000 ./tests`). The longer CLI timeout permits
  editor imports during parallel builds; assertions are unchanged.
- Collaboration server: **40 passed**.
- Baseline ThinkTogether health/auth/refresh/files/CSRF/RBAC: **151 passed**,
  with 18 reported pre-existing test warnings.
- Current-head local restore: **123 public tables** with exact row counts and
  revision `tt_20261115_02`; temporary restore database removed. Archive/evidence:
  `.learnhouse/thinktogether-backups/d9eff538c3b44880b02b21e2cca7fdce.*`.

API, worker, scheduler and collaboration images were rebuilt/restarted; all eight
local services were reported running. Focused privacy tests additionally verified
zero generic cognitive-provider calls, specialist completion, bank lifecycle,
coached practice and support policies. These test results do not prove external
provider operation, production capacity or real multi-participant media behavior.
The earlier synthetic 8-room API/DB load run does not verify 48 real browsers.
Local restore does not establish Neon PITR or R2 object recovery.
Gateway, API and reference health checks returned 200; anonymous readiness
returned 401, and the worker live ping passed.

Local ThinkTogether uses [port 8096](http://localhost:8096/thinktogether/today),
separate from the unchanged reference installation on port 8095. Its API health
endpoint is [port 1349](http://localhost:1349/api/v1/health). LiveKit Cloud
credentials are absent from the local verification setup; live voice/video has
not been demonstrated there.

## Remaining launch gates

Approved original/licensed full PMP content; a licensed specialist-approved
cognitive instrument and adaptive-use policy; actual compatible cohorts; real
merchant/service credentials and successful staging transactions/media/email;
at least 100 genuine expert labels and reviewed model calibration; production
recovery rehearsal and explicit release approval. Current-head local restore
evidence is recorded above; production recovery remains separate.

Draft samples are neither a full approved course nor a validated cognitive
instrument. No tests, calibration command or documentation set the manual launch
acknowledgments. See [RUNBOOK.md](RUNBOOK.md) for the operator evidence required.
