"""Fixed-destination, authenticated API boundary with bounded retry."""
import asyncio
from urllib.parse import quote
import httpx


class Backend:
    def __init__(self, base_url, internal_key, room_uuid, *, client=None, retry_delay=0.5):
        self.url = f'{base_url}/internal/rooms/{quote(room_uuid, safe="")}'
        self.headers = {'X-TT-Internal-Key': internal_key}
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(follow_redirects=False, limits=httpx.Limits(max_connections=8))
        self.retry_delay = retry_delay

    async def _request(self, method, suffix, body=None, *, timeout=8, retry=False):
        for attempt in range(3 if retry else 1):
            try:
                response = await self.client.request(method, self.url + suffix, json=body, headers=self.headers, timeout=timeout)
                if len(response.content) > 131072:
                    raise ValueError('Facilitator API response exceeds limits')
                payload = response.json()
                busy = response.status_code == 409 and ('queue is busy' in str(payload.get('detail')) or 'retry with current context' in str(payload.get('detail')))
                if retry and attempt < 2 and (response.status_code == 503 or busy):
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue
                response.raise_for_status()
                if not isinstance(payload, dict):
                    raise ValueError('Invalid facilitator API response')
                return payload
            except httpx.TransportError:
                if not retry or attempt == 2:
                    raise
                await asyncio.sleep(self.retry_delay * (2 ** attempt))

    async def context(self):
        return await self._request('GET', '/context', timeout=3)

    async def record(self, utterance_id, identity, text):
        return await self._request('POST', '/utterances', {'utterance_id': utterance_id, 'participant_identity': identity, 'text': text}, retry=True)

    async def facilitate(self, utterance_id):
        return await self._request('POST', '/facilitate', {'utterance_id': utterance_id}, timeout=35, retry=True)

    async def close(self):
        if self._owns_client:
            await self.client.aclose()
