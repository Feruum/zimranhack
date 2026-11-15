"""Operational AI usage never contains learning data or grants billing access."""
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.core import TTAuditEvent
from src.services.thinktogether.common import TTContext, utcnow
from src.tests.thinktogether.test_postgres_learning import pg_learning


def context(org_id=1, roles=('billing',)):
    return TTContext(user=SimpleNamespace(id=123), org_id=org_id,
                     is_staff=bool(roles), staff_roles=frozenset(roles))


def sdk_result():
    return SimpleNamespace(output='private cognitive answer', usage=lambda: SimpleNamespace(
        input_tokens=1234, output_tokens=57, requests=2, details={'private': 'learner text'}))


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_postgres_usage_query_preserves_utc_bounds_in_non_utc_session(pg_learning):
    from src.services.thinktogether.ai_usage import usage_summary
    data = pg_learning
    now = datetime(2026, 11, 15, 3, 27, 12, 345678, tzinfo=timezone.utc)
    async with data.engine.begin() as connection:
        await connection.run_sync(lambda sync: TTAuditEvent.__table__.create(sync))
    async with AsyncSession(data.engine, expire_on_commit=False) as db:
        await db.execute(text("SET TIME ZONE 'Asia/Qyzylorda'"))
        db.add(TTAuditEvent(org_id=data.ctx.org_id, action='ai.usage',
            created_at=now, details={'requested_model': 'model',
                'purpose': 'reasoning.grade', 'status': 'succeeded', 'input_tokens': 21,
                'output_tokens': 4, 'provider_requests': 1}))
        await db.commit()
        summary = await usage_summary(db, context(), start=now-timedelta(minutes=1), end=now+timedelta(minutes=1))
        assert summary['totals']['calls'] == 1 and summary['totals']['input_tokens'] == 21
        assert summary['start'] == (now-timedelta(minutes=1)).isoformat()
        excluded = await usage_summary(db, context(), start=now+timedelta(seconds=1), end=now+timedelta(minutes=1))
        assert excluded['totals']['calls'] == 0


async def test_postgres_usage_writer_preserves_utc_in_non_utc_session(pg_learning, monkeypatch, caplog):
    from src.services.thinktogether import ai_usage
    from src.services.thinktogether.ai_usage import make_usage_callback, usage_summary
    data = pg_learning
    now = datetime(2026, 11, 15, 3, 27, 12, 345678, tzinfo=timezone.utc)
    monkeypatch.setattr(ai_usage, 'utcnow', lambda: now)
    monkeypatch.delenv('TT_AI_PRICING_JSON', raising=False)
    async with data.engine.begin() as connection:
        await connection.run_sync(lambda sync: TTAuditEvent.__table__.create(sync))
    @asynccontextmanager
    async def factory():
        async with AsyncSession(data.engine, expire_on_commit=False) as db:
            await db.execute(text("SET LOCAL TIME ZONE 'Asia/Qyzylorda'"))
            yield db
    callback = make_usage_callback(org_id=data.ctx.org_id, requested_model='model',
        purpose='reasoning.grade', session_factory=factory)
    await callback(sdk_result(), duration_ms=4, status='succeeded')
    await callback(None, duration_ms=5, status='failed')
    async with factory() as db:
        events = (await db.exec(select(TTAuditEvent))).all()
        assert len(events) == 2
        assert all(event.created_at == now and event.created_at.utcoffset().total_seconds() == 0 for event in events)
        summary = await usage_summary(db, context(), start=now-timedelta(minutes=1), end=now+timedelta(minutes=1))
        assert summary['totals']['calls'] == 2
        assert summary['totals']['input_tokens'] == 1234
        assert summary['totals']['unknown_usage_calls'] == 1
    assert not [record for record in caplog.records if record.message == 'thinktogether_ai_usage_write_failed']


def test_default_writer_opens_and_closes_session_in_each_celery_event_loop(monkeypatch):
    from src.core.events import database
    from src.services.thinktogether import jobs_worker
    from src.services.thinktogether import ai_usage
    from src.services.thinktogether.ai_usage import make_usage_callback
    opened, committed, closed, events = [], [], [], []
    @asynccontextmanager
    async def fresh_session():
        loop = asyncio.get_running_loop()
        opened.append(loop)
        async def commit():
            assert asyncio.get_running_loop() is loop
            committed.append(loop)
        try:
            yield SimpleNamespace(add=events.append, commit=commit)
        finally:
            closed.append(loop)
    def forbidden_global_pool():
        raise AssertionError('Cross-loop application engine must not be used')
    monkeypatch.setattr(jobs_worker, 'job_session', fresh_session)
    monkeypatch.setattr(database, '_async_session_factory', forbidden_global_pool)
    monkeypatch.delenv('TT_AI_PRICING_JSON', raising=False)
    now = datetime(2026, 11, 15, 3, 27, 12, 345678, tzinfo=timezone.utc)
    monkeypatch.setattr(ai_usage, 'utcnow', lambda: now)
    callback = make_usage_callback(org_id=1, requested_model='model', purpose='reasoning.grade')
    asyncio.run(callback(sdk_result(), duration_ms=2, status='succeeded'))
    asyncio.run(callback(sdk_result(), duration_ms=3, status='succeeded'))
    assert len(opened) == 2
    assert opened[0] is not opened[1] and all(loop.is_closed() for loop in opened)
    assert opened == committed == closed
    assert len(events) == 2
    assert all(event.created_at == now for event in events)
    assert events[0].details['call_uuid'] != events[1].details['call_uuid']


async def test_usage_uses_sdk_counts_and_commits_without_learning_transaction(factory, db, monkeypatch):
    from src.services.thinktogether.ai_usage import make_usage_callback
    monkeypatch.setenv('TT_AI_PRICING_JSON', json.dumps({'configured-model': {
        'input_usd_per_million': 2, 'output_usd_per_million': 8}}))
    callback = make_usage_callback(org_id=1, requested_model='configured-model',
        purpose='reasoning.grade', session_factory=factory)
    await callback(sdk_result(), duration_ms=32, status='succeeded')
    await db.rollback()  # The grade/job may fail after the provider has charged.
    await callback(sdk_result(), duration_ms=34, status='succeeded')
    events = (await db.exec(select(TTAuditEvent))).all()
    assert len(events) == 2
    first, second = (e.details for e in events)
    assert first['input_tokens'] == 1234 and first['output_tokens'] == 57
    assert first['provider_requests'] == 2 and first['duration_ms'] == 32
    assert first['estimated_cost']['usd'] == pytest.approx(.002924)
    assert first['estimated_cost']['basis'] == 'release_configured_estimate'
    assert len(first['estimated_cost']['pricing_version']) == 64
    assert UUID(first['call_uuid']) != UUID(second['call_uuid'])
    assert all(e.actor_user_id is None and not e.resource_id for e in events)
    assert set(first) == {'requested_model', 'purpose', 'call_uuid', 'input_tokens',
                         'output_tokens', 'provider_requests', 'duration_ms', 'status', 'estimated_cost'}
    assert 'private' not in json.dumps(first) and 'cognitive' not in json.dumps(first)


async def test_authoring_usage_keeps_its_purpose_in_audit_and_summary(factory, db, monkeypatch):
    from src.services.thinktogether.ai_usage import make_usage_callback, usage_summary
    monkeypatch.delenv('TT_AI_PRICING_JSON', raising=False)
    start = utcnow()-timedelta(minutes=1)
    for purpose in ('content.generate', 'reasoning.grade'):
        callback = make_usage_callback(org_id=1, requested_model='model',
            purpose=purpose, session_factory=factory)
        await callback(sdk_result(), duration_ms=3, status='succeeded')
    events = (await db.exec(select(TTAuditEvent).order_by(TTAuditEvent.id))).all()
    assert events[0].details['purpose'] == 'content.generate'
    assert events[1].details['purpose'] == 'reasoning.grade'
    summary = await usage_summary(db, context(), start=start, end=utcnow()+timedelta(minutes=1))
    groups = {group['purpose']: group for group in summary['groups']}
    assert set(groups) == {'content.generate', 'reasoning.grade'}
    assert groups['content.generate']['calls'] == 1
    assert groups['reasoning.grade']['calls'] == 1
    assert groups['content.generate']['input_tokens'] == 1234


@pytest.mark.parametrize('raw', ['{', '[]', '{"model":{"input_usd_per_million":-1,"output_usd_per_million":1}}',
    '{"model":{"input_usd_per_million":NaN,"output_usd_per_million":1}}',
    '{"model":{"input_usd_per_million":true,"output_usd_per_million":1}}',
    '{"model":{"input_usd_per_million":"1","output_usd_per_million":1}}',
    '{"model":{"input_usd_per_million":1}}',
    '{"model":{"input_usd_per_million":1,"output_usd_per_million":1,"unknown":2}}'])
def test_release_price_configuration_rejects_invalid_values(raw):
    from src.services.thinktogether.ai_usage import parse_pricing
    with pytest.raises(ValueError):
        parse_pricing(raw)


def test_prices_are_explicit_and_zero_prices_are_valid(monkeypatch):
    from src.services.thinktogether.ai_usage import parse_pricing
    assert parse_pricing('') == {}
    assert parse_pricing('{"model":{"input_usd_per_million":0,"output_usd_per_million":0}}')['model']['input_usd_per_million'] == 0


async def test_provider_failure_records_unknown_counts_without_private_exception(factory, db, monkeypatch):
    from src.services.ai.llm import client
    from src.services.thinktogether.ai_usage import make_usage_callback
    monkeypatch.delenv('TT_AI_PRICING_JSON', raising=False)
    class Agent:
        async def run(self, *args, **kwargs):
            raise RuntimeError('private response and secret credential')
    monkeypatch.setattr(client, 'build_model', lambda _: SimpleNamespace(system='test'))
    monkeypatch.setattr(client, '_agent', lambda *args: Agent())
    callback = make_usage_callback(org_id=1, requested_model='configured-model',
        purpose='reasoning.grade', session_factory=factory)
    with pytest.raises(RuntimeError):
        await client.generate(model_name='configured-model', user_prompt='private prompt', usage_callback=callback)
    event = (await db.exec(select(TTAuditEvent))).one()
    assert event.details['status'] == 'failed'
    assert all(event.details[k] is None for k in ('input_tokens', 'output_tokens', 'provider_requests', 'estimated_cost'))
    assert 'private' not in json.dumps(event.details) and 'secret' not in json.dumps(event.details)


async def test_generate_callback_receives_actual_sdk_result_without_changing_output(monkeypatch):
    from src.services.ai.llm import client
    result, captured = sdk_result(), []
    class Agent:
        async def run(self, *args, **kwargs):
            return result
    async def callback(value, **metadata):
        captured.append((value, metadata))
    monkeypatch.setattr(client, 'build_model', lambda _: SimpleNamespace(system='test'))
    monkeypatch.setattr(client, '_agent', lambda *args: Agent())
    assert await client.generate(model_name='model', user_prompt='private prompt', usage_callback=callback) == result.output
    assert captured[0][0] is result and captured[0][1]['status'] == 'succeeded'
    assert captured[0][1]['duration_ms'] >= 0
    assert await client.generate(model_name='model', user_prompt='private prompt') == result.output


async def test_installed_sdk_usage_property_is_recorded(factory, db, monkeypatch):
    from pydantic_ai.run import AgentRunResult
    from src.services.thinktogether.ai_usage import make_usage_callback
    monkeypatch.delenv('TT_AI_PRICING_JSON', raising=False)
    result = AgentRunResult(output='private cognitive answer')
    result.usage.input_tokens = 321
    result.usage.output_tokens = 19
    result.usage.requests = 1
    callback = make_usage_callback(org_id=1, requested_model='model',
        purpose='reasoning.grade', session_factory=factory)
    await callback(result, duration_ms=3, status='succeeded')
    event = (await db.exec(select(TTAuditEvent))).one()
    assert event.details['input_tokens'] == 321
    assert event.details['output_tokens'] == 19
    assert event.details['provider_requests'] == 1


async def test_callback_failure_never_discards_correct_response_or_logs_body(monkeypatch, caplog):
    from src.services.ai.llm import client
    class Agent:
        async def run(self, *args, **kwargs):
            return sdk_result()
    async def callback(*args, **kwargs):
        raise ValueError('private learner answer credential')
    monkeypatch.setattr(client, 'build_model', lambda _: SimpleNamespace(system='test'))
    monkeypatch.setattr(client, '_agent', lambda *args: Agent())
    assert await client.generate(model_name='model', user_prompt='private prompt', usage_callback=callback) == sdk_result().output
    assert 'private' not in caplog.text and 'credential' not in caplog.text
    assert any(getattr(r, 'error_class', '') == 'ValueError' for r in caplog.records)


async def test_summary_is_billing_only_org_scoped_bounded_and_has_no_raw_events(factory, db, monkeypatch):
    from src.services.thinktogether.ai_usage import make_usage_callback, usage_summary
    from src.routers.thinktogether.usage import staff_usage
    monkeypatch.delenv('TT_AI_PRICING_JSON', raising=False)
    now = utcnow()
    for org_id in (1, 2):
        callback = make_usage_callback(org_id=org_id, requested_model='model',
            purpose='reasoning.grade', session_factory=factory)
        await callback(sdk_result(), duration_ms=12, status='succeeded')
    db.add(TTAuditEvent(org_id=1, action='ai.usage', created_at=now-timedelta(days=40),
        details={'requested_model': 'old-model', 'input_tokens': 99999}))
    db.add(TTAuditEvent(org_id=1, actor_user_id=123, action='learning.private',
        details={'answer': 'private cognitive answer'}))
    await db.commit()
    start, end = now-timedelta(hours=1), utcnow()+timedelta(seconds=1)
    summary = await staff_usage(start=start, end=end, ctx=context(), db=db)
    assert summary['totals']['calls'] == 1
    assert summary['totals']['input_tokens'] == 1234
    assert summary['totals']['provider_requests'] == 2
    assert summary['totals']['estimated_cost_usd'] is None
    assert summary['totals']['unpriced_calls'] == 1
    serialized = json.dumps(summary)
    assert all(s not in serialized for s in ('call_uuid', 'user_id', 'private', 'cognitive', 'old-model'))
    for roles in ((), ('expert',), ('operator',), ('psychometric',)):
        with pytest.raises(HTTPException) as denied:
            await staff_usage(start=start, end=end, ctx=context(roles=roles), db=db)
        assert denied.value.status_code == 403
    for left, right in ((start, start), (end, start), (now-timedelta(days=32), now)):
        with pytest.raises(HTTPException) as invalid:
            await usage_summary(db, context(), start=left, end=right)
        assert invalid.value.status_code == 422


async def test_invalid_prices_and_broken_usage_keep_counts_unavailable(factory, db, monkeypatch, caplog):
    from src.services.thinktogether.ai_usage import make_usage_callback
    monkeypatch.setenv('TT_AI_PRICING_JSON', 'not valid private JSON')
    callback = make_usage_callback(org_id=1, requested_model='model',
        purpose='reasoning.grade', session_factory=factory)
    def broken_usage():
        raise ValueError('private provider body')
    await callback(SimpleNamespace(usage=broken_usage), duration_ms=1, status='succeeded')
    event = (await db.exec(select(TTAuditEvent))).one()
    assert event.details['input_tokens'] is None and event.details['estimated_cost'] is None
    assert 'private' not in caplog.text


async def test_rejected_rubric_output_and_retry_both_keep_provider_usage(factory, db, monkeypatch):
    from src.services.ai.llm import client
    from src.services.thinktogether import ai_usage, learning_evidence
    monkeypatch.setenv('TT_REASONING_MODEL', 'model')
    monkeypatch.delenv('TT_AI_PRICING_JSON', raising=False)
    make_callback = ai_usage.make_usage_callback
    monkeypatch.setattr(ai_usage, 'make_usage_callback', lambda **kwargs: make_callback(**kwargs, session_factory=factory))
    result = sdk_result()
    result.output = learning_evidence.ReasoningGrade(score=.8, feedback='private feedback',
        dimensions={'unexpected_dimension': .8})
    class Agent:
        async def run(self, *args, **kwargs):
            return result
    monkeypatch.setattr(client, 'build_model', lambda _: SimpleNamespace(system='test'))
    monkeypatch.setattr(client, '_agent', lambda *args: Agent())
    item = SimpleNamespace(kind='reasoning', org_id=1, prompt='private scenario',
                           rubric={'dimensions': ['reasoning']})
    for _ in range(2):
        grade = await learning_evidence.score_response(item, {'text': 'private answer'})
        assert grade[0] is None and grade[1] == 'pending'
        await db.rollback()
    events = (await db.exec(select(TTAuditEvent))).all()
    assert len(events) == 2 and events[0].details['call_uuid'] != events[1].details['call_uuid']
    assert all(e.details['input_tokens'] == 1234 for e in events)
    assert 'private' not in json.dumps([e.details for e in events])


async def test_independent_writer_failure_keeps_correct_generation(factory, monkeypatch, caplog):
    from src.services.ai.llm import client
    from src.services.thinktogether.ai_usage import make_usage_callback
    class Agent:
        async def run(self, *args, **kwargs):
            return sdk_result()
    def unavailable_factory():
        raise RuntimeError('private database credentials')
    monkeypatch.setattr(client, 'build_model', lambda _: SimpleNamespace(system='test'))
    monkeypatch.setattr(client, '_agent', lambda *args: Agent())
    callback = make_usage_callback(org_id=1, requested_model='model',
        purpose='facilitator.reasoning', session_factory=unavailable_factory)
    assert await client.generate(model_name='model', user_prompt='private prompt', usage_callback=callback) == sdk_result().output
    assert 'private' not in caplog.text and 'credentials' not in caplog.text
    assert any(getattr(r, 'error_class', '') == 'RuntimeError' for r in caplog.records)
