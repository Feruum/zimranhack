# ThinkTogether shared voice facilitator

One named LiveKit Cloud agent joins a real cohort room. Each authorized, explicitly consenting participant gets a separate Nova-3 STT stream. Final utterances retain the authenticated LiveKit identity and enter one bounded FIFO. One neutral English voice (Cartesia Sonic 3, Robyn) speaks the API's exact persisted facilitator message.

The worker uses Python **3.13 on Debian Bookworm/glibc**, isolated from the API's Python 3.14 Alpine image. LiveKit's RTC and inference wheels are not installed into the API. The base image digest and `livekit-agents==1.8.10` are pinned.

## Configuration

Required worker environment:

| Variable | Meaning |
| --- | --- |
| `LIVEKIT_URL` | LiveKit Cloud project's `wss://…livekit.cloud` URL |
| `LIVEKIT_API_KEY` | Server API key for the same project |
| `LIVEKIT_API_SECRET` | Matching secret |
| `TT_INTERNAL_KEY` | Shared internal API credential; identical on API and worker |
| `TT_API_URL` | Defaults to `http://api:9000/api/v1/tt` |
| `TT_TTS_VOICE` | Optional voice override; default `f31cc6a7-c1e8-4764-980c-60a361443dd1` (Robyn, neutral Australian English) |

The API also requires its existing AI configuration (`LEARNHOUSE_AI_API_KEY` or the legacy Gemini key, Google provider). `TT_FACILITATOR_MODEL` defaults to `gemini-3.5-flash`. Reasoning uses the existing Pydantic AI provider layer. There is **no simulated facilitator answer when credentials or a provider are unavailable**: the API returns 503 and the worker remains silent. The worker validates missing required configuration before registration.

For the pilot, use English cohort language. STT and TTS are explicitly English; another cohort language requires validating and changing the voice/STT configuration together before deployment.

## Build and run

From the repository root:

```sh
docker build -t thinktogether-voice:local apps/voice-agent
docker run --rm --env-file .env.voice --network YOUR_APPLICATION_NETWORK thinktogether-voice:local
docker run --rm thinktogether-voice:local python -m unittest discover -s tests -v
```

Create `.env.voice` in deployment configuration, with the required variables above. Keep it outside the Docker build context. The worker needs outbound access only to the configured API and LiveKit Cloud's media/inference endpoints. It has no database credentials, arbitrary URL fetching, grading, membership, or payment tools. Inference STT/TTS use the LiveKit credentials; separate Deepgram/Cartesia keys are unnecessary.

The API must mount `src.routers.thinktogether.facilitator.router` under `/api/v1/tt` and migrate both tables in `src.db.thinktogether.facilitator`. While holding the room row lock, invoke `dispatch_facilitator(room)` when activating room media. It lists existing dispatches before creating named agent `thinktogether-facilitator` with JSON metadata `{"room_uuid":"<room UUID>"}`. Holding the database room lock serializes dispatch requests across API replicas.

## Authorization and persistence

The worker connects with `SUBSCRIBE_NONE`. Only microphone tracks whose exact `tt:{org_id}:{room_uuid}:{user_uuid}` identity appears in the API's authorized consent list are subscribed or sent to STT. Its own track, camera, screen audio, forged identities and nonconsenting participants are excluded. Revoking one participant closes only that participant's transcription stream. The other consenting members continue.

The internal endpoints require `X-TT-Internal-Key`:

1. `GET /internal/rooms/{room_uuid}/context` rechecks phase, enabled status, membership, verified identity, entitlement and each person's audio consent.
2. `POST /internal/rooms/{room_uuid}/utterances` persists deduplicated `{utterance_id, participant_identity, text}`.
3. `POST /internal/rooms/{room_uuid}/facilitate` accepts only `{utterance_id}`. It loads the saved utterance and approved shared context, calls the real model with a structured response schema, rechecks current authorization, and atomically commits the facilitator turn plus public room event. It returns `{id, text, revision}`; a structured `wait` returns empty text and creates no speech event.

The API's per-room database CAS lease serializes reasoning across API processes. Model calls have a 25-second provider timeout and a 27-second outer deadline; leases expire after 90 seconds for crash recovery. No database transaction remains open during reasoning. An active lease returns 409 with `Retry-After`. Durable unique turn IDs and deterministic public message IDs make HTTP retry safe. Every saved turn records model and prompt versions. Failed provider calls release their lease and create no fabricated public event.

The model sees an explicit allowlist: shared case prompt/choices, operator-approved board summary, recent public chat, consenting speakers' recent voice utterances, and roles. It does not receive diagnostic results, private attempts, cognitive profiles, correct answers, payment data, attachments, or tools. Discussion data is serialized as JSON in the user message, separate from system instructions. Output is a short validated clarify/reflect/invite/wait decision; arbitrary actions and URLs are rejected. Prompt boundaries reduce injection risk but do not prove semantic correctness: educational review and live adversarial checks remain necessary.

Context is rechecked every 500 ms, before recording, after generation and while TTS plays. Phase changes, moderator disable, consent changes or API authorization outages cancel in-flight speech, clear queued PCM and unsubscribe denied tracks. The local audio queue is capped at 200 ms. Poll interval plus API request latency and remote WebRTC buffers are the practical stop bound; already transmitted audio cannot be recalled. The API independently prevents new publication after a phase/consent stop.

## Verification and external gates

Credential-free tests exercise concurrent speaker ordering, attribution, deduplication, individual consent, forged/own audio exclusion, track gating, speech cancellation, exact TTS text and safe API retries. API tests cover structured provider boundaries, durable idempotency, lease contention/recovery, phase/consent rechecks and dispatch contracts. These tests use controlled provider/transport doubles and do not claim cloud success.

Before admitting a live pilot: configure the real Cloud project and Gemini API key; verify one named agent across 4–6 real browser participants; test overlap, join/reconnect and speaker identity; withdraw one person's consent while another continues; advance discussion to transfer during speech; disable the agent; compare heard TTS with the durable chat event; test Cloud/STT/LLM/TTS outage recovery and measure the actual stop latency. Live media, provider billing and educational quality require those external credentials and people.

Official sources consulted for this implementation:

- [LiveKit multi-user transcriber example](https://github.com/livekit/agents/blob/main/examples/other/transcription/multi-user-transcriber.py)
- [LiveKit inference STT API](https://docs.livekit.io/reference/python/livekit/agents/inference/stt.html)
- [LiveKit inference TTS API](https://docs.livekit.io/reference/python/livekit/agents/inference/tts.html)
- [Cartesia supported models and voice catalog](https://docs.livekit.io/agents/models/tts/cartesia/)
- [Named agent dispatch](https://docs.livekit.io/agents/server/agent-dispatch/)
- [Agent dispatch protobuf contract](https://github.com/livekit/protocol/blob/main/protobufs/livekit_agent_dispatch.proto)
