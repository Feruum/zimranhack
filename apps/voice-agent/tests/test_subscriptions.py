import asyncio
from types import SimpleNamespace
import unittest

from thinktogether_voice.core import ConsentSubscriptions


class Publication:
    def __init__(self, sid, kind='audio', source='microphone'):
        self.sid, self.kind, self.source = sid, kind, source
        self.track = object()
        self.subscriptions = []
    def set_subscribed(self, value): self.subscriptions.append(value)


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_explicit_consent_starts_audio_and_revocation_cancels_one_track(self):
        allowed = {'alice', 'bob'}
        started, cancelled = [], []
        async def transcribe(track, identity):
            started.append(identity)
            try: await asyncio.Event().wait()
            finally: cancelled.append(identity)
        gate = ConsentSubscriptions(lambda: allowed, transcribe, audio_kind='audio', microphone_source='microphone')
        pubs = {name: Publication(name) for name in ['alice', 'bob', 'eve', 'agent']}
        participants = [SimpleNamespace(identity=name, track_publications={name: publication}) for name, publication in pubs.items()]
        await gate.sync(participants)
        await asyncio.sleep(0)
        self.assertEqual(set(started), {'alice', 'bob'})
        self.assertEqual(pubs['eve'].subscriptions, [False])
        self.assertEqual(pubs['agent'].subscriptions, [False])
        allowed.remove('alice')
        await gate.sync(participants)
        self.assertIn('alice', cancelled)
        self.assertNotIn('bob', cancelled)
        self.assertEqual(pubs['alice'].subscriptions[-1], False)
        await gate.close()

    async def test_camera_and_screen_audio_are_never_transcribed(self):
        started = []
        async def transcribe(track, identity): started.append(identity)
        gate = ConsentSubscriptions(lambda: {'alice'}, transcribe, audio_kind='audio', microphone_source='microphone')
        camera, screen = Publication('camera', 'video'), Publication('screen', source='screen_audio')
        await gate.sync([SimpleNamespace(identity='alice', track_publications={'camera': camera, 'screen': screen})])
        await asyncio.sleep(0)
        self.assertEqual(started, [])
        self.assertEqual(camera.subscriptions, [False])
        self.assertEqual(screen.subscriptions, [False])
        await gate.close()
