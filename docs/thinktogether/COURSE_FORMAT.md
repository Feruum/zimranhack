# Course creation demo: three chapters

One **Create a course** form accepts a topic/brief, pasted material and optional
PDF, DOCX, UTF-8 TXT/Markdown, CSV or JSON files. Previously uploaded organization
materials can be selected too. A book is one possible input, not a separate
product or a required step. Images/audio/video and scanned PDFs need a text/OCR
conversion first; the interface does not claim to read unsupported formats.

The default UI creates exactly three chapters. Existing full-PMP API authoring
remains available as `course_format=full_pmp`; it is separate from this small demo.
A published three-chapter demo never satisfies full-PMP sales/readiness gates.

## Agent contracts

The course architect proposes three chapters. For each chapter, the lesson agent
writes Markdown with the following `tt-course-md-v1` directives:

```md
# Chapter title

:::explain
The concept, explained in ordinary Markdown.
:::

:::example
A worked example in a new context.
:::

:::reflection
What information would change your decision?
:::

:::exercise c1_q1
The learning objective for the future question.
{{csv:c1_q1}}
:::
```

Each chapter has three exercise markers (`c1_q1`–`c1_q3`, then `c2_…`, `c3_…`).
Nested/unknown directives, repeated IDs, unmatched endings and missing markers
are invalid. Markers inside ordinary fenced code remain code, not instructions.

A second, separate model invocation receives the chapter and these markers and
returns typed question records. The application writes RFC4180 CSV with exactly
this column order (so model-generated quote escaping cannot corrupt the file):

```csv
id,kind,prompt,choices_json,answer_key_json,explanation,source_refs_json
```

`kind` is `single_choice` or `multiple_choice`; the JSON cells contain the same
choice/key structures as ordinary ThinkTogether questions. `source_refs_json`
is an array of `{id,quote}` references to material excerpts (empty when no
material was provided). Formula cells, invalid JSON, wrong choice keys, extra
questions and unfilled markers are rejected.

The deterministic insertion algorithm creates real `TTItem` records and replaces
each `{{csv:c1_q1}}` with `{{item:<public UUID>}}`. This is not a third model call.
The normal lesson reader parses the directives into text/example/reflection and
exercise components. Exercise contents and answers use authenticated server
delivery; CSV answer keys are never embedded in public lesson Markdown.
Inline lesson attempts are coached and cannot establish independent mastery.
Experts can preview chapters as they appear and download each generated CSV.

## Material handling and evidence

Uploads are limited to 10 MB and five selected files per course. Extracted text
is stored privately in organization-scoped durable records; names never become
filesystem paths. The parser rejects encrypted PDFs, unsupported files, missing
text and oversized documents instead of silently truncating them. Scanned PDFs
need OCR first. Text extraction is bounded at 2 million characters per material.

Each generation step selects at most ten relevant excerpts of about 2,600
characters. This is passage retrieval, not a claim that the model reads every
page in one call. Provided-text instructions cannot change the agents' roles.
References must identify supplied excerpts and match an actual short quotation.
Invented references fail validation. Expert factual/license review remains required.

The run saves eight checkpoints: outline, three writing steps, three CSV/insertion
steps, and final validation. The existing durable queue, SSE/polling, retries,
stop controls, draft editing, expert approval and separate publication are reused.

## Course-only OpenRouter configuration

`TT_CONTENT_ENABLED=true`, `TT_CONTENT_PROVIDER=openrouter`,
`TT_CONTENT_MODEL=deepseek/deepseek-v4.1-flash-1017` and `TT_CONTENT_API_KEY`
configure this pipeline without changing global grading or voice models. Keep
the credential only in the ignored local environment, never in public runtime
configuration, source bundles or browser requests. The provider/model reference
was verified against the [official OpenRouter model page](https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017).
Course-only OpenRouter calls use low reasoning effort so thinking tokens do not
consume the chapter's entire output budget. An overall 180-second deadline bounds
SDK transport/output retries as well as each call's 120-second HTTP timeout.

With no course-specific provider, the existing ThinkTogether AI settings are used.
API, worker and web must use the updated code together. No database migration is
needed. AI output is always draft content until a human explicitly approves and
publishes it.

Live verification used the selected DeepSeek model through the deployed API and
worker to persist a complete three-chapter draft with nine CSV exercises. All
eight checkpoints completed, material references and insertion passed, and a
coached exercise delivery exposed no answer key. The course remains
`ready_for_review`; it was not approved or published. The input was an original
synthetic test note. Evidence is retained in the ignored local QA folder.
