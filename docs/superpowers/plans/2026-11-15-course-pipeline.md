# ThinkTogether document course pipeline

**Goal:** Implement the supplied two-agent protocol inside the existing ThinkTogether expert workspace.

**Architecture:** Reuse authenticated material uploads, TTJob persistence and the existing worker/scheduler. A new job kind checkpoints the validated Markdown after the first DeepSeek call. The second call produces CSV; Python validates it and renders three SVG files in temporary private storage, then stores the complete artifact package in the job row. Authenticated artifact routes serve the persisted package. No migration, public file storage, voice or collab changes are needed.

**Source contract:** The supplied Course Architect and Diagram Architect prompts remain versioned backend resources. Model is `deepseek-flash`, thinking enabled, reasoning effort `medium`. Only backend environment variables contain credentials.

- [x] Add synthetic Markdown/CSV fixtures and failing tests for malformed markers, graph integrity, escaping, missing artifacts, provider failures and authorization.
- [x] Implement strict Markdown/CSV validation, deterministic layouts, atomic final assembly and bounded relevant source selection with explicit coverage metadata.
- [x] Add provider client and resumable job orchestration using existing material and permission services. Persist source selection, draft and artifacts privately in TTJob.
- [x] Add staff-only job/status/artifact endpoints and register the worker handler.
- [x] Add a minimal Russian file/topic form and Markdown/SVG preview to ExpertContent, preserving existing authoring features.
- [x] Run new and existing ThinkTogether unit/router tests, TypeScript and targeted lint checks. Check the supplied PPTX extraction. Document exact Windows launch commands and actual verification results.

Test commands: `docker exec -w /app/api thinktogether-api-1 .venv/bin/python -m pytest src/tests/thinktogether/test_course_pipeline.py -q`; `bun x tsc --noEmit --incremental false` from apps/web. Tests use fixed model responses and never submit paid calls.
