import json
import unittest
import httpx

from thinktogether_voice.backend import Backend


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_preserves_utterance_id_and_exact_server_text(self):
        calls = []
        async def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(503, json={'detail': 'provider unavailable'})
            return httpx.Response(200, json={'id': 'message-1', 'text': 'Exact approved question?', 'revision': 4})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = Backend('http://api:9000/api/v1/tt', 'internal-test-key', 'room', client=client, retry_delay=0)
            result = await api.facilitate('utterance-1')
        self.assertEqual(result['text'], 'Exact approved question?')
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(json.loads(r.content) == {'utterance_id': 'utterance-1'} for r in calls))
        self.assertTrue(all(r.headers['X-TT-Internal-Key'] == 'internal-test-key' for r in calls))

    async def test_phase_stop_is_not_retried(self):
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx.Response(409, json={'detail': 'Voice facilitator is disabled in this room phase'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = Backend('http://api:9000/api/v1/tt', 'key', 'room', client=client, retry_delay=0)
            with self.assertRaises(httpx.HTTPStatusError): await api.facilitate('utterance-1')
        self.assertEqual(len(calls), 1)
