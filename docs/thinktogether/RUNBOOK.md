# ThinkTogether operations runbook

The local project, the reference ThinkTogether installation, staging and production are
separate deployments. The reference installation on port 8095 is not modified by
the ThinkTogether Compose project on port 8096. Do not copy local credentials to
staging or production.

See [STATUS.md](STATUS.md) for implemented scope and outstanding evidence. The
source declares **42 ThinkTogether tables**, targeting Alembic head
**`tt_20261115_02`**. Current local migration/restore evidence is recorded below;
staging and production verification remain separate.

## Local start and verification

From the repository root in PowerShell:

```powershell
./docker/thinktogether/setup-local.ps1 -Start
```

This creates an ignored local environment with generated secrets if absent, builds
the project and starts its own PostgreSQL, Redis, API, worker, scheduler, collaboration
server, web and gateway. Existing local settings are retained. Browse
`http://localhost:8096/thinktogether/today`; the API health endpoint is
`http://localhost:1349/api/v1/health`. The bootstrap administrator email is in
`docker/thinktogether/.env`; its generated password is there too. Never paste that
file into a bug report. In the Expert workspace, draft samples demonstrate the
authoring flow and remain unsuitable for sale or diagnostics.

The optional local voice process needs genuine LiveKit Cloud credentials. After
configuring them and the API's Google provider key, run this from
`docker/thinktogether`:

```powershell
docker compose --profile voice up -d voice-agent
```

The named agent is dispatched by the API for discussion, with a separate consent
check for every speaker. A video join does not imply speech processing consent.
Missing providers return an unavailable state. There is no fabricated payment,
cognitive result or fallback AI answer.

LiveKit Cloud credentials are absent from the current local verification setup;
live voice/video provider operation has not been demonstrated there. Starting a
process or exercising provider mocks does not establish a working media session.

## Verification commands

Use a rebuilt API image containing the source under review. From the repository
root in PowerShell:

```powershell
$ttCompose = @('--env-file', 'docker/thinktogether/.env', '-f', 'docker/thinktogether/docker-compose.yml')
docker compose @ttCompose exec -T api /app/api/.venv/bin/python cli.py migrate --status
docker compose @ttCompose exec -T api /app/api/.venv/bin/python -m pytest src/tests/thinktogether -q
docker compose @ttCompose exec -T api /app/api/.venv/bin/python scripts/verify_thinktogether_migration.py
```

The migration verifier uses a disposable PostgreSQL schema for upgrade, metadata
parity, downgrade and re-upgrade. PostgreSQL concurrency tests additionally need
an authorized `TT_TEST_DATABASE_URL` using `postgresql+asyncpg`, provided through
the test runner's secret environment. Without it, those tests **skip**; a green
SQLite run alone does not verify PostgreSQL locks. Never point these test helpers
at production. Run one complete API test container at a time on the local host.

The focused cognitive privacy regression is
`src/tests/thinktogether/test_cognitive_ai_privacy.py`. It verifies zero generic
provider calls/jobs for open cognitive responses, safe completion of old queued
jobs, deterministic closed items, specialist completion and ordinary PMP grading.
Practice-bank, authored-mechanics and initial-support regressions are in
`test_learning_bank.py`, `test_learning_mechanics.py` and `test_learning_support.py`.

From `apps/web`:

```powershell
bun test tests/thinktogether
bun x tsc --noEmit --pretty false
bun x eslint components/ThinkTogether services/thinktogether "tests/thinktogether*.mjs" --no-warn-ignored
```

Commands must target the source being released. Recorded local results below
apply to the final tested source; test counts alone do not establish external
provider operation or browser/media capacity. Preserve actual outputs with the
release evidence.

## Content, assessments and grading

Programs and content follow draft, review, approved and published states with
staff grants and approval evidence. Published course content is frozen; changes
require versions. Full-program publication checks the `PMP-2026-07` blueprint,
all 26 task mappings, prerequisite graph, authored lessons and two separate
held-out 180-question, 240-minute mock forms with the configured domain balance.
Each skill also needs at least three distinct published independent case
families outside lesson examples, mock forms, coached material and published
diagnostics. Draft instruments do not remove cases from the live bank. Publishing
an instrument must leave this minimum available in every affected published
program, including family aliases across versions. These checks do not replace
expert review of content quality or rights.

Goals and domain/cognitive diagnostics precede admission to learning cohorts.
Licensed cognitive instruments require informed consent, specialist approval and
an explicit versioned support policy with adaptive-use rights. Its expert-authored
criterion rules can recommend initial chunks, new-material limits and worked
examples; observed independent performance takes priority afterward. No population
norms, Gf/Gc cutoffs or intelligence labels are fabricated.

Closed cognitive items are scored by their approved keys. **Open cognitive
reasoning is specialist-only**, even when `TT_REASONING_MODEL` is configured.
Existing queued cognitive grading jobs complete without contacting the generic
educational LLM and leave the assessment awaiting specialist review. Per-item
cognitive scores/keys remain hidden from learners; results belong to their
private assessment profile. Do not put raw psychometric answers or reports into
AI prompts, telemetry or educational grading calibration datasets.

Retell requires reading, closing the source and explaining; ThinkFirst requires
critiquing an authored flawed AI answer. Both are coached, followed by a fresh
independent case on the same principle. QuestPath prioritizes a fresh same-skill
case after an error. Coached/group/hint-exposed responses never establish
independent mastery. Missing authored material produces a content shortage, not
generated substitute content.

Ordinary PMP reasoning uses the configured educational grader through durable
jobs; outages remain pending or require expert review. Before approving that
model, supply **at least 100 genuine expert-labeled educational reasoning
examples**, with versioned rubrics, reviewer provenance and rationales. Keep the
authorized dataset in ignored `.learnhouse/grading-evaluation/expert.jsonl`.
From the repository root, using `$ttCompose` above:

```powershell
docker compose @ttCompose run --rm --no-deps -v "${PWD}/.learnhouse/grading-evaluation:/evaluation" api /app/api/.venv/bin/python scripts/evaluate_thinktogether_grading.py --input /evaluation/expert.jsonl --check-input
docker compose @ttCompose run --rm --no-deps -v "${PWD}/.learnhouse/grading-evaluation:/evaluation" api /app/api/.venv/bin/python scripts/evaluate_thinktogether_grading.py --input /evaluation/expert.jsonl --output /evaluation/report.json
```

`--check-input` is offline and grants no calibration approval. The second command
requires real model/provider configuration and reports agreement, confusion,
disagreements, unavailable grades and dataset/model/rubric/grader versions. Review
every disagreement and unavailable result. Neither command invents annotations
or sets a release flag. Usage costs, when configured, are estimates rather than
provider invoices.

## Production configuration

`docker/thinktogether/production.compose.yml` is an independent deployment file,
not an override for the local file. It uses immutable release image tags, external
Neon PostgreSQL, private R2 storage and a private persistent Redis. No database,
Redis, API or collaboration port is published. The gateway binds only loopback.

Copy `docker/thinktogether/production.env.example` to the ignored
`.learnhouse/thinktogether-production.env` and fill it using the deployment's secret
manager. This file must not enter the source archive or Git. Use a separate file,
project name, secrets, bucket and provider project for staging. The Compose CLI's
`--env-file` supplies interpolation values; `TT_ENV_FILE` points services at the
same file relative to `docker/thinktogether`.

| Service | Required operator work |
| --- | --- |
| Neon | Create separate staging/production databases and roles. Enable `vector`. Supply TLS connection URLs; use a direct endpoint as `TT_MIGRATION_DATABASE_URL`, and the runtime URL as `LEARNHOUSE_SQL_CONNECTION_STRING`. Set the account's restore window and verify an actual restore to a separate branch. |
| R2 | Create a private bucket with both `r2.dev` access and custom public domains disabled. Use a bucket-scoped S3 token via `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, region `auto` and the account endpoint. Files pass application authorization before streaming or short-lived presigning. Test unauthenticated object access and a revoked entitlement. |
| Resend | Verify the sender domain, configure the API key and sender address. Send real verification and recovery email in staging. Learning reminder email additionally requires the learner's explicit notification preference. |
| Gemini | Set the Google provider API key and pin `TT_REASONING_MODEL` and `TT_FACILITATOR_MODEL` to evaluated model IDs. Retain the evaluated model, prompt and rubric release versions. A model change requires a new evaluation, including at least 100 expert-labeled ordinary educational reasoning responses and investigation of every disagreement. Psychometric answers are excluded. |
| LiveKit Cloud | Use one project per environment. Put its URL/key/secret on API and voice worker; share only `TT_INTERNAL_KEY` between those services. No audio/video recording is configured. Test 4–6 real browsers, consent withdrawal, speaker attribution, reconnect and phase-change cancellation. |
| Polar | Configure separate sandbox/production organizations and products. API/webhook version is `2026-10`. Point the signed webhook at `/api/v1/tt/billing/webhooks/polar`. Configure one-time fixed-price product terms through the expert billing interface; browser redirects never grant access. Verify sandbox purchase/refund/reconciliation before the approved production merchant account is used. |
| Sentry | API uses `LEARNHOUSE_SENTRY_DSN`; web uses `NEXT_PUBLIC_LEARNHOUSE_SENTRY_DSN`. Keep DSNs blank until a staging check confirms that request bodies, responses, cognitive results, transcripts, tokens and replay content are excluded or scrubbed. Verify an error reaches the correct environment without private test data. |

Neon documents its [TLS connection requirements](https://neon.com/docs/connect/connect-securely)
and [branch controls](https://neon.com/docs/manage/branches). Cloudflare documents
the [two ways a bucket can become public](https://developers.cloudflare.com/r2/buckets/public-buckets/).
Account-specific retention, regional availability and access settings must be
checked in those accounts; they are not established by the local deployment.

At the outer TLS reverse proxy, preserve the public `Host`, overwrite
`X-Forwarded-For` with the client address, support WebSocket upgrade, disable proxy
buffering for SSE, and forward to `http://127.0.0.1:8096`. Redirect public HTTP to
HTTPS. `production.nginx.conf` asserts HTTPS because only that trusted TLS proxy
should reach its loopback listener. Do not expose this listener directly to the
internet. Configure certificate issuance/renewal on that proxy. Set every public
URL to the same HTTPS origin and test secure, host-scoped session cookies.

## Build, migrate and release

Before building, regenerate the exact corresponding source archive:

```powershell
./scripts/bundle-thinktogether-source.ps1
```

The archive is packaged into the web image at `/thinktogether-source.tar.gz` and
linked in the application. It must contain the complete release source, license
and build instructions and exclude environment files, credentials, runtime data
and dependency directories. Verify download and inspect the archive in staging.
If `TT_SOURCE_URL` points to an external release instead, it must provide the same
complete source for that running release. Do not point at an unrelated upstream
commit.

Build the API and collaboration images with the local Compose build definitions;
build web with `LEARNHOUSE_PUBLIC=true` and the voice image from `apps/voice-agent`.
Tag and push all four as `${TT_IMAGE_PREFIX}-{api,web,collab,voice}:${TT_RELEASE}`.
Record image digests and Git revision in the release record. Never use `latest`
for a release that must be recoverable.

For an **existing stamped database**, from `docker/thinktogether`:

```powershell
docker compose --env-file ../../.learnhouse/thinktogether-production.env -f production.compose.yml pull
docker compose --env-file ../../.learnhouse/thinktogether-production.env -f production.compose.yml --profile operations run --rm --no-deps migrate
docker compose --env-file ../../.learnhouse/thinktogether-production.env -f production.compose.yml up -d
docker compose --env-file ../../.learnhouse/thinktogether-production.env -f production.compose.yml ps
```

First rehearse the migration and restore on staging, then take a backup before
production migration. `migrate` invokes Alembic directly; it does not reconcile
models against the database. The API forces `LEARNHOUSE_AUTO_MIGRATE=false` and
refuses to start when the recorded migration head differs from the release.
Worker and scheduler start after API health succeeds. Never use a running
production API as a migration tool.

The upstream migration chain starts from a pre-existing base schema. A **brand-new
empty database** therefore needs a one-time, explicit bootstrap before API startup:
run the project API's `cli.py install --short` with the installation's administrator
settings and direct database connection, then confirm `cli.py migrate --status`.
That operator-invoked bootstrap creates the initial model schema and stamps its
head; it must not be used to repair an existing production database. The normal
release path above is explicit versioned Alembic migration only. Fresh Neon
bootstrap still needs to be rehearsed with real account/TLS credentials.

If verification fails, keep the previous images available and stop the rollout.
Use a reviewed compatible rollback or restore to a separate database, verify it,
and switch the deployment DSN deliberately. Do not downgrade paid learner data
blindly. A backup file is not a restore test.

## Backup and restore drill

The local drill is deliberately restricted to `thinktogether-db-1` and database
`thinktogether`. It creates a custom PostgreSQL archive, copies it into the ignored
worktree `.learnhouse/thinktogether-backups`, restores a separately named
`tt_restore_<random-id>` database, compares every public table's row count and
the migration revision, writes JSON evidence with SHA-256, then removes only that
validated temporary database. It leaves the source database and reference
ThinkTogether untouched.

```powershell
./scripts/thinktogether-backup-restore.ps1
```

Concurrent writes which change table counts make the drill fail; quiesce local
writes and rerun. Archive and JSON evidence may contain private production data
if reused elsewhere; this script refuses external/production targets. For actual
production, use the organization's restricted backup tooling and verify both
Neon restore and private R2 object recovery in a separate environment. Establish
retention, encryption, access and restore-time objectives in the release record.

## API/DB load harness

From `docker/thinktogether`:

```powershell
docker compose run --rm --no-deps api /app/api/.venv/bin/python scripts/thinktogether_load_check.py --isolated-local-check
```

The harness creates and removes its own `tt_load_<random-id>` PostgreSQL schema.
It runs eight rooms with six **synthetic** identities each, personal tasks,
concurrent room chat, CAS retries, independent transfer, reconnect/resume and
duplicate submissions through the in-process FastAPI HTTP interface. It checks
one immutable attempt per submitted delivery, consecutive durable room events,
withheld private solutions, different next tasks after different answers, and
absence of one-day/group-derived mastery. Fixtures never enter the public schema
or count as real groups, licensed diagnostics or expert-approved content.

Authentication is supplied by an explicit test dependency. This test measures
the API and real local PostgreSQL, **not** deployed authentication, gateway/SSE
delivery, browser capacity, Yjs or video/audio. The required 8-room × 6-browser
LiveKit/load exercise remains a separate credentialed staging test. Capture CPU,
memory, DB connections, queue delay, reconnect behavior and media/provider costs
for that test. Do not turn these local timings into a production capacity claim.

## Operating and admitting the paid pilot

Check health externally and monitor API error rate/latency, worker liveness,
pending/retry/dead `tt_job` age, webhook inbox age, scoring latency, expert review
backlog, group occupancy, provider spend and independent delayed outcomes. Logs
must contain identifiers and error types, not responses, cognitive results or
credentials. Do not include raw `.env` or complete provider error bodies in
support requests. Dead jobs require investigation; manual requeue follows a
recorded fix and does not bypass idempotency.

For a payment dispute, inspect the local order and its current authoritative
Polar order, then reconcile. A delayed checkout or webhook must not activate a
second membership. Full confirmed refund revokes entitlement and queues live
media removal; partial refund alone preserves access. With a provider outage,
retain attempts and retry durable processing. Use the expert review queue rather
than issuing a successful fabricated AI score. If voice fails, use text/board.

Inspect the Expert readiness view and `/api/v1/tt/readiness` with a staff session.
The offer remains closed until the complete program, licensed cognitive
instrument, real compatible groups, mail/video/AI/payment configuration, AI
evaluation, production restore rehearsal and release approval are established.
Flags `TT_POLAR_PRODUCTION_APPROVED`, `TT_AI_EVALUATION_APPROVED`,
`TT_RESTORE_DRILL_PASSED` and `TT_RELEASE_APPROVED` are manual evidence
acknowledgments. This runbook and the local tests do not set them.

## Recorded local verification and restore

Final local verification recorded on 2026-11-15:

| Check | Recorded result |
| --- | --- |
| ThinkTogether API | 200 passed, including 7 real PostgreSQL tests; 102.94 seconds |
| Full web suite (`bun test --isolate --timeout 20000 ./tests`) | 743 passed, 0 failed; 2,025 assertions, 69 files, 19.14 seconds |
| Collaboration server | 40 passed |
| Baseline ThinkTogether health/auth/refresh/files/CSRF/RBAC | 151 passed; 18 reported pre-existing test warnings |
| Migration round trip | 42 ThinkTogether tables: upgrade, metadata parity, downgrade and re-upgrade passed |
| Local schema | Alembic head `tt_20261115_02` |
| Local health/access | Gateway, API and reference health returned 200; anonymous readiness returned 401; worker live ping passed |

API, worker, scheduler and collaboration images were freshly rebuilt/restarted;
all eight local services were reported running. These results do not establish
real provider transactions, production recovery or LiveKit session capacity.

The final local archive/restore drill completed at
**2026-11-15T01:29:02+05:00** (2026-11-14 UTC). A separate temporary database
matched **123 public tables**, their exact row counts and revision
**`tt_20261115_02`**. The temporary database was removed, and worker/scheduler
were restarted. Evidence is
`.learnhouse/thinktogether-backups/d9eff538c3b44880b02b21e2cca7fdce.json`, with
the corresponding `.dump` archive (**580,695 bytes**). Its SHA-256 is:

```text
1AE1B31E9F3F42E11CDFEDEBC224646C94F04665038324FFB87DF036D32A79BF
```

This verifies the isolated local PostgreSQL restore only; **Neon PITR and R2
object recovery were not exercised**. The 123-table count includes legacy public
tables as well as the 42 ThinkTogether tables. It supersedes the earlier
122-table restore at `tt_20261114_01`, whose evidence remains under
`.learnhouse/thinktogether-backups/4bd80b2057894aed826384602dedb578.*`.

### Earlier synthetic load observation

The isolated API/DB load run completed **736 requests**, including **120 expected
CAS conflicts** which were retried successfully, over **8 rooms / 48 synthetic
participants**. It persisted exactly **96 attempts** despite duplicate sends and
completed in **27.907 seconds** on the current development host: 26.37 requests/s,
HTTP-handler latency p50 802.67 ms, p95 4533.06 ms, max 5640.02 ms. No failed
assertions occurred. These timings include lock contention in the synthetic
burst; they are observations, not an SLO. External media, real sessions, provider
availability and production resources were not tested.
These earlier measurements do not certify the current release source.
