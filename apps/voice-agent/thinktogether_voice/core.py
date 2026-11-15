"""Credential-free dialogue coordination; LiveKit transport lives in worker.py."""
import asyncio
from collections import deque
from dataclasses import dataclass
import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    api_url: str
    internal_key: str
    livekit_url: str
    livekit_key: str
    livekit_secret: str
    tts_voice: str

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        for key in ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'TT_INTERNAL_KEY'):
            if not env.get(key, '').strip():
                raise ValueError(f'{key} is required for the ThinkTogether voice worker')
        api_url = env.get('TT_API_URL', 'http://api:9000/api/v1/tt').rstrip('/')
        for value, schemes in ((api_url, {'http', 'https'}), (env['LIVEKIT_URL'], {'wss', 'ws'})):
            parsed = urlsplit(value)
            if parsed.scheme not in schemes or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('TT_API_URL and LIVEKIT_URL must be absolute service URLs without credentials or query strings')
        # Robyn: neutral mature Australian English voice, LiveKit Inference catalog.
        return cls(api_url, env['TT_INTERNAL_KEY'], env['LIVEKIT_URL'], env['LIVEKIT_API_KEY'], env['LIVEKIT_API_SECRET'],
                   env.get('TT_TTS_VOICE', 'f31cc6a7-c1e8-4764-980c-60a361443dd1'))


class DialogueQueue:
    """One bounded FIFO for every consenting speaker, one output voice.

    Only final STT segments enter this queue. Identity is the authenticated
    LiveKit participant identity, never a name inferred by speech recognition.
    The API remains the authority for consent, entitlement and room phase.
    """
    def __init__(self, api, output, *, own_identity, max_pending=64):
        self.api, self.output, self.own_identity = api, output, own_identity
        self.allowed = frozenset()
        self._queue = asyncio.Queue(maxsize=max_pending)
        self._seen = set()
        self._seen_order = deque()
        self._active = None
        self._closed = False

    def set_context(self, context):
        allowed = set()
        if context.get('phase') == 'discussion' and context.get('agent_enabled') is True:
            room_uuid = context.get('room_uuid')
            livekit_room = context.get('livekit_room', '')
            org_id = livekit_room.removeprefix('tt-').split('-', 1)[0]
            prefix = f'tt:{org_id}:{room_uuid}:'
            for participant in context.get('participants', []):
                identity = participant.get('identity', '')
                if isinstance(identity, str) and identity.startswith(prefix) and len(identity) > len(prefix) and identity != self.own_identity:
                    allowed.add(identity)
        if frozenset(allowed) != self.allowed:
            if self._active and not self._active.done():
                self._active.cancel()
            self.output.clear()
        self.allowed = frozenset(allowed)

    def submit(self, utterance_id, identity, text):
        if self._closed or identity not in self.allowed or not text.strip() or len(text) > 8000 or utterance_id in self._seen:
            return False
        try:
            self._queue.put_nowait((utterance_id, identity, text))
        except asyncio.QueueFull:
            logger.warning('Voice dialogue queue is full; dropping segment')
            return False
        self._seen.add(utterance_id)
        self._seen_order.append(utterance_id)
        if len(self._seen_order) > 4096:
            self._seen.discard(self._seen_order.popleft())
        return True

    async def _turn(self, utterance_id, identity, text):
        self.set_context(await self.api.context())
        if identity not in self.allowed:
            return
        await self.api.record(utterance_id, identity, text)
        response = await self.api.facilitate(utterance_id)
        self.set_context(await self.api.context())
        if identity in self.allowed and response.get('text'):
            await self.output.speak(response['text'])

    async def run(self):
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                if item[1] not in self.allowed:
                    continue
                self._active = asyncio.create_task(self._turn(*item))
                try:
                    await self._active
                except asyncio.CancelledError:
                    if self._closed:
                        return
                except Exception:
                    # No transcript, key or provider exception body in logs.
                    logger.warning('Facilitator turn unavailable; no substitute answer was generated')
                    self.output.clear()
            finally:
                self._active = None
                self._queue.task_done()

    async def join(self):
        await self._queue.join()

    async def close(self):
        self._closed = True
        if self._active:
            self._active.cancel()
        self.output.clear()
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._queue.put_nowait(None)


class ConsentSubscriptions:
    """Explicit subscription gate; denied tracks never reach an STT factory."""
    def __init__(self, allowed, transcribe, *, audio_kind, microphone_source):
        self.allowed, self.transcribe = allowed, transcribe
        self.audio_kind, self.microphone_source = audio_kind, microphone_source
        self.tasks = {}

    async def sync(self, participants):
        desired = {}
        for participant in participants:
            microphone_selected = False
            for publication in participant.track_publications.values():
                accept = (participant.identity in self.allowed() and publication.kind == self.audio_kind
                          and publication.source == self.microphone_source and not microphone_selected)
                publication.set_subscribed(accept)
                if accept:
                    microphone_selected = True
                    if publication.track is not None:
                        desired[(participant.identity, publication.sid)] = publication.track
        for key, task in list(self.tasks.items()):
            if key not in desired or task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.tasks[key]
        for key, track in desired.items():
            if key not in self.tasks:
                self.tasks[key] = asyncio.create_task(self.transcribe(track, key[0]))

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
