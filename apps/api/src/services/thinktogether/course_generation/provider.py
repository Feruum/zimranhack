import asyncio
import os

import httpx

from .validator import ProviderError

MODEL = 'deepseek-flash'


def configured():
    return bool(os.getenv('DEEPSEEK_API_KEY', '').strip())


async def complete(system, user, *, max_tokens=24000, client=None):
    key = os.getenv('DEEPSEEK_API_KEY', '').strip()
    if not key:
        raise ProviderError('Задайте DEEPSEEK_API_KEY на сервере API и в worker.')
    payload = {'model': MODEL, 'reasoning_effort': 'medium', 'thinking': {'type': 'enabled'},
        'max_tokens': max_tokens, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
    async def request(http):
        try:
            response = await asyncio.wait_for(http.post('https://api.deepseek.com/chat/completions',
                json=payload, headers={'Authorization': 'Bearer '+key}, timeout=httpx.Timeout(210, connect=15)), timeout=240)
            response.raise_for_status()
            choice = response.json()['choices'][0]
            content = choice['message']['content']
            if choice.get('finish_reason') != 'stop' or not isinstance(content, str) or not content.strip():
                raise ProviderError('DeepSeek вернул пустой или обрезанный ответ. Повторите генерацию с более коротким материалом.')
            return content
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
            raise ProviderError('Не удалось получить полный ответ DeepSeek. Проверьте ключ, баланс и доступность API.') from None
    if client is not None:
        return await request(client)
    async with httpx.AsyncClient() as http:
        return await request(http)
