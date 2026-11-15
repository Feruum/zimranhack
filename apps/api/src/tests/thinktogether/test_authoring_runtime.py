"""Course generation has a bounded provider budget, isolated from other AI uses."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.services.thinktogether import authoring_provider, content_generation as generation


def test_course_openrouter_reasoning_has_a_small_budget(monkeypatch):
    monkeypatch.setenv('TT_CONTENT_PROVIDER', 'openrouter')
    monkeypatch.setenv('TT_CONTENT_ENABLED', 'true')
    monkeypatch.setenv('TT_CONTENT_API_KEY', 'test-credential')
    model = authoring_provider.build_authoring_model('deepseek/deepseek-v4.1-flash-1017')
    assert model.settings['extra_body']['reasoning'] == {'effort': 'low'}


class Output(BaseModel):
    value: str


def patch_provider(monkeypatch, generate):
    from src.services.ai.llm import client
    from src.services.thinktogether import ai_usage
    monkeypatch.setattr(client, 'generate', generate)
    monkeypatch.setattr(authoring_provider, 'build_authoring_model', lambda name: object())
    monkeypatch.setattr(ai_usage, 'make_usage_callback', lambda **kwargs: None)


async def test_authoring_deadline_includes_sdk_retries(monkeypatch):
    cancelled = asyncio.Event()

    async def hanging(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    patch_provider(monkeypatch, hanging)
    monkeypatch.setattr(generation, 'CALL_DEADLINE_SECONDS', 0.01, raising=False)
    job = SimpleNamespace(org_id=1, payload={'model': 'test', 'request': {'course_format': 'demo_three_chapters'}})
    # The enclosing timeout protects the regression test itself if no deadline exists.
    task = asyncio.create_task(generation.ask(job, Output, {}))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.5)
        assert task.done(), 'The authoring deadline must cancel the provider call itself'
        assert cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_chapter_prompt_omits_full_pmp_question_settings(monkeypatch):
    from src.services.thinktogether.course_demo import ChapterDraft
    captured = {}

    async def provider(**kwargs):
        captured.update(json.loads(kwargs['user_prompt']))
        return ChapterDraft(markdown='Original chapter text. '*25, objectives=['Explain.', 'Apply.'])

    patch_provider(monkeypatch, provider)
    request = generation.GenerationInput(idempotency_key='test').model_dump()
    job = SimpleNamespace(org_id=1, payload={'model': 'test', 'request': request})
    await generation.ask(job, ChapterDraft, {'instruction': 'Write the chapter.'})
    assert 'weeks' not in captured['request']
    assert 'practice_items_per_skill' not in captured['request']
    assert not captured.get('answer_formats')
