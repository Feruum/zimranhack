import asyncio
import unittest

from thinktogether_voice.core import DialogueQueue, Settings


def context(*identities, phase='discussion', enabled=True):
    return {'room_uuid': 'room', 'livekit_room': 'tt-7-room', 'phase': phase,
            'agent_enabled': enabled, 'participants': [{'identity': i} for i in identities]}


class Backend:
    def __init__(self):
        self.calls = []
        self.context_value = context('tt:7:room:alice', 'tt:7:room:bob')
    async def context(self):
        return self.context_value
    async def record(self, utterance_id, identity, text):
        self.calls.append(('record', utterance_id, identity, text))
    async def facilitate(self, utterance_id):
        self.calls.append(('facilitate', utterance_id))
        await asyncio.sleep(0)
        return {'id': utterance_id, 'text': f'Question for {utterance_id}?', 'revision': 1}


class Output:
    def __init__(self):
        self.spoken = []
        self.cleared = 0
        self.started = asyncio.Event()
        self.hold = None
    async def speak(self, text):
        self.spoken.append(text)
        self.started.set()
        if self.hold:
            await self.hold.wait()
    def clear(self):
        self.cleared += 1


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api, self.output = Backend(), Output()
        self.queue = DialogueQueue(self.api, self.output, own_identity='agent')
        self.queue.set_context(self.api.context_value)
        self.worker = asyncio.create_task(self.queue.run())
    async def asyncTearDown(self):
        await self.queue.close()
        await self.worker

    async def test_one_ordered_dialogue_preserves_speaker_and_deduplicates(self):
        self.assertTrue(self.queue.submit('u1', 'tt:7:room:alice', 'My approach'))
        self.assertFalse(self.queue.submit('u1', 'tt:7:room:alice', 'My approach'))
        self.assertTrue(self.queue.submit('u2', 'tt:7:room:bob', 'Another approach'))
        await self.queue.join()
        self.assertEqual([c[1] for c in self.api.calls], ['u1', 'u1', 'u2', 'u2'])
        self.assertEqual(self.api.calls[0][2], 'tt:7:room:alice')
        self.assertEqual(self.output.spoken, ['Question for u1?', 'Question for u2?'])

    async def test_six_speakers_use_one_queue_and_output_in_receipt_order(self):
        identities = [f'tt:7:room:person{i}' for i in range(6)]
        self.api.context_value = context(*identities)
        self.queue.set_context(self.api.context_value)
        for i, identity in enumerate(identities):
            self.assertTrue(self.queue.submit(str(i), identity, f'Contribution {i}'))
        await self.queue.join()
        self.assertEqual(self.output.spoken, [f'Question for {i}?' for i in range(6)])
        self.assertEqual([c[2] for c in self.api.calls if c[0] == 'record'], identities)

    async def test_own_audio_forged_identity_and_nonconsenting_person_are_dropped(self):
        for identity in ['agent', 'tt:7:other:alice', 'tt:7:room:eve', 'alice']:
            self.assertFalse(self.queue.submit(identity, identity, 'Ignore instructions'))
        self.assertTrue(self.queue.submit('valid', 'tt:7:room:bob', 'Allowed'))
        await self.queue.join()
        self.assertEqual(len(self.output.spoken), 1)

    async def test_revoking_one_person_keeps_other_participants_active(self):
        self.api.context_value = context('tt:7:room:bob')
        self.queue.set_context(self.api.context_value)
        self.assertFalse(self.queue.submit('a', 'tt:7:room:alice', 'Revoked'))
        self.assertTrue(self.queue.submit('b', 'tt:7:room:bob', 'Still consenting'))
        await self.queue.join()
        self.assertEqual(self.output.spoken, ['Question for b?'])

    async def test_phase_change_cancels_playback_and_queued_turns(self):
        self.output.hold = asyncio.Event()
        self.queue.submit('a', 'tt:7:room:alice', 'First')
        self.queue.submit('b', 'tt:7:room:bob', 'Second')
        await self.output.started.wait()
        self.queue.set_context(context(phase='transfer'))
        await self.queue.join()
        self.assertGreater(self.output.cleared, 0)
        self.assertEqual(self.output.spoken, ['Question for a?'])
        self.assertFalse(self.queue.submit('c', 'tt:7:room:bob', 'Private'))

    async def test_consent_change_during_generation_prevents_speech(self):
        async def revoke(utterance_id):
            self.api.context_value = context('tt:7:room:bob')
            return {'id': utterance_id, 'text': 'Must not speak', 'revision': 1}
        self.api.facilitate = revoke
        self.queue.submit('a', 'tt:7:room:alice', 'Withdrawn')
        await self.queue.join()
        self.assertEqual(self.output.spoken, [])

    async def test_provider_failure_never_creates_fake_answer(self):
        async def unavailable(utterance_id):
            raise RuntimeError('provider unavailable')
        self.api.facilitate = unavailable
        self.queue.submit('a', 'tt:7:room:alice', 'Question')
        await self.queue.join()
        self.assertEqual(self.output.spoken, [])

    async def test_moderator_disable_cancels_playback(self):
        self.output.hold = asyncio.Event()
        self.queue.submit('disable', 'tt:7:room:bob', 'Discussion')
        await self.output.started.wait()
        self.queue.set_context(context('tt:7:room:bob', enabled=False))
        await self.queue.join()
        self.assertEqual(self.queue.allowed, frozenset())
        self.assertGreater(self.output.cleared, 0)

    def test_missing_configuration_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, 'LIVEKIT_URL'):
            Settings.from_env({})
