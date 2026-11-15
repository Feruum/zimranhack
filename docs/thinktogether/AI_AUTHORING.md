# Live AI course authoring

**Current demo default:** the unified Create a course form now creates exactly
three chapters from a topic, pasted text and optional uploaded materials, using
a writing agent, an exercise agent and deterministic Markdown/CSV insertion.
See [COURSE_FORMAT.md](COURSE_FORMAT.md) for this flow and course-only OpenRouter
configuration. The 26-skill full-PMP pipeline below remains available only when
`course_format=full_pmp` is explicitly requested.

In **Core → Expert workspace → Content → Create a PMP course with AI**, an
authorized content expert enters the title, audience, language, preparation weeks
and an optional brief. The server queues a durable generation run and returns
immediately. This is actual provider-backed authoring; missing AI configuration
returns 503 and never substitutes sample content.

## Pipeline

1. AI proposes an ordered prerequisite graph covering the 26 task identifiers in
   the pinned PMP July 2026 blueprint.
2. Each skill receives an original lesson and 3–10 separate practice families
   (five by default). The worked example has a different family.
3. Two held-out mock forms are generated in ten-question batches. Each contains
   180 original questions, a 240-minute timer, and domain quotas of 59/74/47.
4. Structural validation checks references, coverage, independent banks and mock
   separation. The run becomes **ready for review**, not published or approved.
5. A human expert inspects and edits the saved draft fields, records review
   evidence, and explicitly approves the package. A separate action publishes
   the approved package through the existing publication gates.

The outline and each completed lesson/batch are committed with the run checkpoint.
SSE sends committed snapshots; a four-second HTTP poll provides session refresh
and recovery. Reopening the page resumes the run view. Redis is transport, not
the record of progress. A failed step rolls back its content; retries resume that
step without duplicating previously saved records. Stop waits for the current
transaction/provider call and preserves drafts. At most two active runs per
organization are admitted. A complete run has 64 saved steps; duration depends
on the configured model, not a simulated progress timer.

Expert edits are restricted to unapproved drafts. Returning an approved package
to draft revokes its prior approvals. Batch approval/publication are transactional;
an invalid component rolls back the package action. Students cannot see these
drafts, and no public or learner generation endpoint exists. The generator never
creates cognitive assessment instruments or claims to validate psychometrics.

## Configuration

- `LEARNHOUSE_IS_AI_ENABLED=true`
- Configure the existing ThinkTogether AI provider and credentials, e.g.
  `LEARNHOUSE_AI_PROVIDER=google` and `LEARNHOUSE_AI_API_KEY`.
- `TT_CONTENT_MODEL` specifies the authoring model; when empty, it falls back
  to `TT_REASONING_MODEL`. The resolved model and prompt version are pinned in
  the run. The actual provider call uses the existing Pydantic AI adapter.
- Keep `worker` and `scheduler` running with the updated API source.

Each provider call emits content-free usage under `content.generate` using the
existing usage callback. Provider errors persist only their exception class.
No learner answers or cognitive profiles enter the generation prompt.

API: `POST/GET /api/v1/tt/content/generations`, `GET /{id}`, `GET /{id}/events`,
`POST /{id}/cancel`, `POST /{id}/retry`, and
`POST /{id}/review/{approve|request_changes|publish}`. Start requires an
idempotency key; reusing it with changed settings returns 409.
`PATCH /api/v1/tt/content/drafts/{programs|lessons|items}/{id}` edits allowed
draft fields and records an audit event. All endpoints require current content
permissions and organization scope; streaming rechecks access each tick.

The generator uses existing `tt_job` and learning tables, so this feature adds
no database migration. The authenticated UI, API and worker must be rebuilt
together. Automated tests use an explicitly stubbed provider in a test database;
they do not demonstrate a live paid model call or expert factual validation.

Blueprint reference: [PMI official examination content outline](https://www.pmi.org/-/media/pmi/documents/public/pdf/certifications/new-pmp-examination-content-outline-2026.pdf?rev=af7ae1c6006f43009fee4f613a350f9b).
Generated course text and cases are original drafts; the public outline does not
authorize copying real examination questions or proprietary training materials.
