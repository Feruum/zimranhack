"""Persistent support conversations, tenant boundaries and command replay."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.core import TTAuditEvent, TTNotification
from src.db.thinktogether.learning import TTAttempt, TTDiagnosticRun, TTSupportCase
from src.services.thinktogether.common import TTContext


def context(user_id=101, org_id=1, roles=()):
    return TTContext(user=SimpleNamespace(id=user_id), org_id=org_id,
                     is_staff=bool(roles), staff_roles=frozenset(roles))


async def create(db, ctx=None, **overrides):
    from src.services.thinktogether import support
    values = dict(idempotency_key='create-1', kind='learning', title='A question',
                  message='Please help with my learning plan.')
    values.update(overrides)
    return await support.create_ticket(db, ctx or context(), **values)


@pytest.mark.asyncio
async def test_support_creation_persists_and_replays_across_sessions(db, engine):
    from src.services.thinktogether import support
    first = await create(db)
    async with AsyncSession(engine, expire_on_commit=False) as other:
        again = await create(other)
        assert again['id'] == first['id']
        assert again['messages'] == first['messages']
        assert (await support.list_tickets(other, context()))[0]['title'] == 'A question'
        assert len((await other.exec(select(TTSupportCase))).all()) == 1
        assert len((await other.exec(select(TTAuditEvent))).all()) == 1
        with pytest.raises(HTTPException) as conflict:
            await create(other, message='A different request')
        assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_self_list_stays_self_only_and_staff_queue_requires_support_permission(db):
    from src.services.thinktogether import support
    first = await create(db)
    await create(db, context(102), idempotency_key='create-2')
    await create(db, context(103, org_id=2))
    assert [r['id'] for r in await support.list_tickets(db, context())] == [first['id']]
    assert await support.list_tickets(db, context(104, roles=('expert',))) == []
    assert len(await support.list_tickets(db, context(104, roles=('operator',)), staff=True)) == 2
    with pytest.raises(HTTPException) as denied:
        await support.list_tickets(db, context(105, roles=('psychometric',)), staff=True)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_attempt_link_requires_same_learner_and_org_and_never_serializes_answers(db):
    from src.services.thinktogether import support
    run = TTDiagnosticRun(org_id=1, user_id=101, instrument_id=1, kind='cognitive')
    db.add(run)
    await db.flush()
    attempt = TTAttempt(org_id=1, user_id=101, item_id=1, skill_id=1, family_id='private',
                        idempotency_key='attempt', request_hash='hash', diagnostic_id=run.id,
                        response={'text': 'private cognitive answer'})
    db.add(attempt)
    await db.commit()
    for ctx in (context(102), context(101, org_id=2), context(103, roles=('expert',))):
        with pytest.raises(HTTPException) as denied:
            await create(db, ctx, attempt_id=attempt.public_id)
        assert denied.value.status_code == 404
    ticket = await create(db, attempt_id=attempt.public_id)
    assert ticket['attempt_id'] == attempt.public_id
    staff_ticket = (await support.list_tickets(db, context(102, roles=('expert',)), staff=True))[0]
    assert staff_ticket['attempt_id'] is None
    assert 'private cognitive answer' not in str(staff_ticket)
    assert 'response' not in str(staff_ticket)


@pytest.mark.asyncio
async def test_messages_are_owner_or_support_only_and_replays_do_not_duplicate(db):
    from src.services.thinktogether import support
    ticket = await create(db)
    for ctx in (context(102), context(102, roles=('psychometric',)), context(101, org_id=2)):
        with pytest.raises(HTTPException) as denied:
            await support.add_message(db, ctx, ticket['id'], 'reply-1', 'A reply')
        assert denied.value.status_code == 404
    first = await support.add_message(db, context(), ticket['id'], 'reply-1', 'A reply')
    again = await support.add_message(db, context(), ticket['id'], 'reply-1', 'A reply')
    assert len(first['messages']) == len(again['messages']) == 2
    with pytest.raises(HTTPException) as conflict:
        await support.add_message(db, context(), ticket['id'], 'reply-1', 'Changed reply')
    assert conflict.value.status_code == 409
    staff = context(104, roles=('expert',))
    await support.add_message(db, staff, ticket['id'], 'staff-1', 'We are reviewing this.')
    await support.add_message(db, staff, ticket['id'], 'staff-1', 'We are reviewing this.')
    notices = (await db.exec(select(TTNotification))).all()
    assert len(notices) == 1 and notices[0].user_id == 101
    assert notices[0].href == '/thinktogether/profile#support'
    assert len((await db.exec(select(TTAuditEvent))).all()) == 3


@pytest.mark.asyncio
async def test_status_is_permission_gated_audited_notified_and_idempotent(db):
    from src.services.thinktogether import support
    ticket = await create(db, kind='billing')
    with pytest.raises(HTTPException) as denied:
        await support.set_status(db, context(), ticket['id'], 'status-1', 'resolved', 'Answered')
    assert denied.value.status_code == 403
    staff = context(104, roles=('billing',))
    result = await support.set_status(db, staff, ticket['id'], 'status-1', 'resolved', 'Answered')
    replay = await support.set_status(db, staff, ticket['id'], 'status-1', 'resolved', 'Answered')
    assert result['status'] == replay['status'] == 'resolved'
    assert len(result['messages']) == 2
    assert len((await db.exec(select(TTNotification))).all()) == 1
    audits = (await db.exec(select(TTAuditEvent))).all()
    assert len(audits) == 2
    assert audits[-1].action == 'support.status_changed'
    assert audits[-1].details == {'previous_status': 'open', 'status': 'resolved'}
    with pytest.raises(HTTPException) as conflict:
        await support.set_status(db, staff, ticket['id'], 'status-1', 'open', 'Answered')
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_staff_messages_and_notification_roll_back_together(db, monkeypatch):
    from src.services.thinktogether import support
    from src.services.thinktogether import notifications
    ticket = await create(db)
    original = notifications.notify_user

    async def fail_after_notice(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('notification failure')

    monkeypatch.setattr(notifications, 'notify_user', fail_after_notice)
    with pytest.raises(RuntimeError):
        await support.add_message(db, context(104, roles=('expert',)), ticket['id'], 'staff-1', 'Response')
    await db.rollback()
    saved = (await support.list_tickets(db, context()))[0]
    assert len(saved['messages']) == 1
    assert not (await db.exec(select(TTNotification))).all()
    assert len((await db.exec(select(TTAuditEvent))).all()) == 1


@pytest.mark.asyncio
async def test_concurrent_sessions_append_without_overwriting_messages(db, tmp_path):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel
    from src.services.thinktogether import support

    # A file database gives each session an independent connection and real
    # transaction locks; the suite's in-memory StaticPool cannot prove this.
    concurrent_engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "support.sqlite"}')
    try:
        async with concurrent_engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(concurrent_engine, expire_on_commit=False) as session:
            ticket = await create(session)

        async def append(key):
            async with AsyncSession(concurrent_engine, expire_on_commit=False) as session:
                return await support.add_message(session, context(), ticket['id'], key, key)

        await asyncio.gather(*(append(key) for key in ['one', 'two', 'three', 'one']))
        async with AsyncSession(concurrent_engine, expire_on_commit=False) as session:
            messages = (await support.list_tickets(session, context()))[0]['messages']
            assert len(messages) == 4
            assert {m['message'] for m in messages[1:]} == {'one', 'two', 'three'}
    finally:
        await concurrent_engine.dispose()


@pytest.mark.asyncio
async def test_item_shortage_queue_projects_only_safe_operational_details(db):
    from src.services.thinktogether import support
    db.add(TTSupportCase(org_id=1, user_id=101, kind='item_shortage', dedup_key='shortage',
        details={'program_id': 2, 'reason': 'No unseen approved items', 'skill_ids': [4, 5],
                 'email': 'private@example.test', 'response': {'text': 'secret'}}))
    await db.commit()
    item = (await support.list_tickets(db, context(104, roles=('expert',)), staff=True))[0]
    assert item['kind'] == 'item_shortage' and item['status'] == 'open'
    assert item['details'] == {'program_id': 2, 'reason': 'No unseen approved items', 'skill_ids': [4, 5]}
    assert 'secret' not in str(item) and 'private@example.test' not in str(item)


@pytest.mark.asyncio
async def test_support_http_contract_and_bounded_inputs(db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from src.routers.thinktogether import support as routes
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/tt')
    app.dependency_overrides[routes.get_context] = lambda: context()

    async def session():
        yield db

    app.dependency_overrides[routes.get_db_session] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        body = dict(idempotency_key='http-1', kind='learning', title='Help', message='My question')
        result = await client.post('/api/v1/tt/support/tickets', json=body)
        assert result.status_code == 200
        identifier = result.json()['id']
        assert (await client.get('/api/v1/tt/support/tickets')).json()[0]['id'] == identifier
        assert (await client.post(f'/api/v1/tt/support/tickets/{identifier}/messages',
            json={'idempotency_key': 'http-reply', 'message': 'More context'})).status_code == 200
        assert (await client.get('/api/v1/tt/staff/support')).status_code == 403
        for invalid in ({'message': ' '}, {'title': 'x' * 201}, {'kind': 'refund'},
                        {'attempt_id': 'not-a-uuid'}, {'idempotency_key': 'x' * 129}, {'org_id': 2}):
            assert (await client.post('/api/v1/tt/support/tickets', json=body | invalid)).status_code == 422
        app.dependency_overrides[routes.get_context] = lambda: context(104, roles=('expert',))
        assert (await client.get('/api/v1/tt/staff/support')).json()[0]['id'] == identifier
        updated = await client.patch(f'/api/v1/tt/staff/support/{identifier}',
            json={'idempotency_key': 'http-status', 'status': 'in_progress', 'message': 'Investigating'})
        assert updated.status_code == 200 and updated.json()['status'] == 'in_progress'
        resolved = await client.post(f'/api/v1/tt/staff/support/{identifier}/resolve',
            json={'idempotency_key': 'http-resolve'})
        assert resolved.status_code == 200 and resolved.json()['status'] == 'resolved'
