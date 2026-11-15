"""LiveKit Cloud worker: separate attributed STT streams, one shared TTS track.

No LLM runs here: the API projects approved context, invokes Pydantic AI, checks
current authorization, and persists the exact text before this worker speaks.
"""
import asyncio
import hashlib
import json
import logging
from uuid import UUID, uuid4

from livekit import rtc
from livekit.agents import AgentServer, AutoSubscribe, JobContext, cli, inference, stt

from .backend import Backend
from .core import ConsentSubscriptions, DialogueQueue, Settings

logger = logging.getLogger('thinktogether.voice')
server = AgentServer()


class VoiceOutput:
    def __init__(self, voice):
        self.source = rtc.AudioSource(24000, 1, queue_size_ms=200)
        self.track = rtc.LocalAudioTrack.create_audio_track('ThinkTogether AI facilitator', self.source)
        self.tts = inference.TTS(model='cartesia/sonic-3', voice=voice, language='en', sample_rate=24000)

    async def publish(self, room):
        await room.local_participant.publish_track(self.track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    async def speak(self, text):
        # A single TTS model and voice for the whole room. Text is exactly the
        # API's durable agent_message payload; no local paraphrase or reply.
        async with asyncio.timeout(50):
            async with self.tts.synthesize(text) as stream:
                async for event in stream:
                    await self.source.capture_frame(event.frame)
            await self.source.wait_for_playout()

    def clear(self):
        self.source.clear_queue()

    async def close(self):
        self.clear()
        await self.tts.aclose()
        await self.source.aclose()


class RoomWorker:
    def __init__(self, ctx, settings, room_uuid):
        self.ctx, self.settings, self.room_uuid = ctx, settings, room_uuid
        self.api = Backend(settings.api_url, settings.internal_key, room_uuid)
        self.output = VoiceOutput(settings.tts_voice)
        self.dialogue = DialogueQueue(self.api, self.output, own_identity=ctx.room.local_participant.identity)
        self.subscriptions = ConsentSubscriptions(lambda: self.dialogue.allowed, self.transcribe,
            audio_kind=rtc.TrackKind.KIND_AUDIO, microphone_source=rtc.TrackSource.SOURCE_MICROPHONE)
        self.tasks = []
        self.closed = False

    async def start(self):
        await self.output.publish(self.ctx.room)
        self.tasks = [asyncio.create_task(self.dialogue.run()), asyncio.create_task(self.watch_context())]

    async def watch_context(self):
        try:
            while not self.closed:
                try:
                    context = await self.api.context()
                    if context.get('room_uuid') != self.room_uuid or context.get('livekit_room') != self.ctx.room.name:
                        raise ValueError('Dispatched room and API authorization do not match')
                    self.dialogue.set_context(context)
                    await self.subscriptions.sync(list(self.ctx.room.remote_participants.values()))
                except Exception:
                    # Fail closed on phase stop, revocation, API/provider outage
                    # or track race. Recheck permits discussion to start later.
                    self.dialogue.set_context({})
                    await self.subscriptions.close()
                await asyncio.sleep(0.5)
        finally:
            self.dialogue.set_context({})
            await self.subscriptions.close()

    async def transcribe(self, track, identity):
        if identity not in self.dialogue.allowed:
            return
        model = inference.STT(model='deepgram/nova-3', language='en', sample_rate=16000,
            extra_kwargs={'endpointing': 500})
        audio = rtc.AudioStream(track, sample_rate=16000, num_channels=1, capacity=10)
        stream = model.stream()
        stream_id = str(uuid4())
        parts, segment_keys = [], set()
        sequence = 0

        def flush():
            nonlocal sequence
            if not parts:
                return
            text = ' '.join(parts).strip()
            parts.clear()
            sequence += 1
            utterance_id = hashlib.sha256(f'{identity}:{track.sid}:{stream_id}:{sequence}'.encode()).hexdigest()
            self.dialogue.submit(utterance_id, identity, text)

        async def feed():
            async for event in audio:
                # The poller cancels the stream on revoke, and this per-frame
                # check prevents new audio crossing the provider boundary.
                if identity not in self.dialogue.allowed:
                    return
                stream.push_frame(event.frame)
            stream.end_input()

        feeder = asyncio.create_task(feed())
        try:
            async for event in stream:
                if identity not in self.dialogue.allowed:
                    return
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                    alternative = event.alternatives[0]
                    text = alternative.text.strip()
                    key = (getattr(event, 'request_id', ''), alternative.start_time, alternative.end_time, text)
                    if text and key not in segment_keys:
                        segment_keys.add(key)
                        parts.append(text)
                        if sum(len(p) for p in parts) > 7000:
                            flush()
                    if len(segment_keys) > 2048:
                        segment_keys.clear()
                elif event.type == stt.SpeechEventType.END_OF_SPEECH:
                    flush()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('A participant transcription stream is unavailable; retrying after authorization refresh')
        finally:
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
            await stream.aclose()
            await audio.aclose()
            await model.aclose()

    async def close(self):
        self.closed = True
        await self.dialogue.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.subscriptions.close()
        await self.output.close()
        await self.api.close()


@server.rtc_session(agent_name='thinktogether-facilitator')
async def entrypoint(ctx: JobContext):
    settings = Settings.from_env()
    metadata = json.loads(ctx.job.metadata or '{}')
    room_uuid = str(UUID(metadata['room_uuid']))
    # No automatic audio subscription: context authorization precedes every STT
    # stream, including participants already present when the job connects.
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_NONE)
    worker = RoomWorker(ctx, settings, room_uuid)
    ctx.add_shutdown_callback(worker.close)
    await worker.start()


if __name__ == '__main__':
    Settings.from_env()  # clear startup error before network registration
    cli.run_app(server)
