"""Exercise the real SDK adapter with local stream doubles, never cloud calls."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from thinktogether_voice import worker


class Stream:
    def __init__(self, events): self.events, self.closed = events, False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): await self.aclose()
    def __aiter__(self): return self.iterate()
    async def iterate(self):
        for event in self.events:
            yield event
    def push_frame(self, frame): pass
    def end_input(self): pass
    async def aclose(self): self.closed = True


class SDKAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_rtc_audio_source_loads_on_debian_glibc(self):
        source = worker.rtc.AudioSource(24000, 1, queue_size_ms=200)
        track = worker.rtc.LocalAudioTrack.create_audio_track('local-sdk-test', source)
        self.assertIsNotNone(track)
        frame = worker.rtc.AudioFrame.create(24000, 1, 240)
        await source.capture_frame(frame)
        source.clear_queue()
        await source.aclose()

    async def test_tts_uses_exact_durable_backend_text_and_single_voice(self):
        spoken, frames = [], []
        async def capture(frame): frames.append(frame)
        async def done(): pass
        source = SimpleNamespace(capture_frame=capture, wait_for_playout=done, clear_queue=lambda: None, aclose=done)
        def synthesize(text):
            spoken.append(text)
            return Stream([SimpleNamespace(frame='pcm-1'), SimpleNamespace(frame='pcm-2')])
        tts = SimpleNamespace(synthesize=synthesize, aclose=done)
        with patch.object(worker.rtc, 'AudioSource', return_value=source), patch.object(worker.rtc.LocalAudioTrack, 'create_audio_track', return_value=object()), patch.object(worker.inference, 'TTS', return_value=tts) as factory:
            output = worker.VoiceOutput('neutral-voice')
            await output.speak('Exact approved question?')
            await output.close()
        self.assertEqual(spoken, ['Exact approved question?'])
        self.assertEqual(frames, ['pcm-1', 'pcm-2'])
        self.assertEqual(factory.call_args.kwargs['voice'], 'neutral-voice')

    async def test_duplicate_stt_segments_form_one_attributed_utterance(self):
        final = SimpleNamespace(type=worker.stt.SpeechEventType.FINAL_TRANSCRIPT, request_id='request',
                                alternatives=[SimpleNamespace(text='Our evidence', start_time=0.0, end_time=1.0)])
        end = SimpleNamespace(type=worker.stt.SpeechEventType.END_OF_SPEECH)
        stream, audio = Stream([final, final, end]), Stream([])
        model = SimpleNamespace(stream=lambda: stream, aclose=stream.aclose)
        submitted = []
        runtime = worker.RoomWorker.__new__(worker.RoomWorker)
        runtime.dialogue = SimpleNamespace(allowed={'tt:7:room:alice'}, submit=lambda *args: submitted.append(args))
        with patch.object(worker.inference, 'STT', return_value=model), patch.object(worker.rtc, 'AudioStream', return_value=audio):
            await runtime.transcribe(SimpleNamespace(sid='track-a'), 'tt:7:room:alice')
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0][1:], ('tt:7:room:alice', 'Our evidence'))
        self.assertTrue(stream.closed and audio.closed)

    async def test_revoked_identity_never_opens_provider_stream(self):
        runtime = worker.RoomWorker.__new__(worker.RoomWorker)
        runtime.dialogue = SimpleNamespace(allowed={'tt:7:room:bob'})
        with patch.object(worker.inference, 'STT', side_effect=AssertionError('Must not send audio')):
            await runtime.transcribe(SimpleNamespace(sid='track-a'), 'tt:7:room:alice')
