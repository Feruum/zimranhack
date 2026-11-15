"""Persistent cohort discussion and personal calendar authorization."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.community import TTCohortMessage
from src.db.thinktogether.billing import TTEntitlement
from src.db.thinktogether.core import TTNotification, TTNotificationPreference
from src.db.thinktogether.learning import TTProgram, TTLearnerProfile, TTPlan, TTReview
from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom
from src.db.users import User
from src.services.thinktogether.common import TTContext
from src.services.thinktogether import community


@pytest.fixture
async def community_setup(db, org, regular_user):
    user = await db.get(User, regular_user.id)
    user.email_verified = True
    db.add(user)
    program = TTProgram(org_id=org.id, title='Community program')
    db.add(program)
    await db.flush()
    cohort = TTCohort(org_id=org.id, program_id=program.id, title='Small group', language='en', timezone='UTC', schedule={}, status='active')
    db.add(cohort)
    await db.flush()
    db.add(TTCohortMember(org_id=org.id, cohort_id=cohort.id, user_id=user.id, status='active'))
    now = datetime.now(timezone.utc)
    entitlement = TTEntitlement(org_id=org.id, user_id=user.id, program_id=program.id, source='complimentary', starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=10))
    db.add(entitlement)
    await db.commit()
    return cohort, TTContext(user=user, org_id=org.id), entitlement


async def test_messages_persist_across_sessions_and_replay_same_key_once(db, engine, community_setup):
    cohort, ctx, _ = community_setup
    first = await community.post_message(db, ctx, cohort.cohort_uuid, 'A **thoughtful** question.', 'one')
    async with AsyncSession(engine, expire_on_commit=False) as fresh:
        repeated = await community.post_message(fresh, ctx, cohort.cohort_uuid, 'A **thoughtful** question.', 'one')
        assert repeated == first
        page = await community.list_messages(fresh, ctx, cohort.cohort_uuid)
        assert page['messages'] == [first]
        assert first['author']['id'] == ctx.user.user_uuid
        assert 'email' not in str(first)
        assert (await fresh.execute(select(func.count()).select_from(TTCohortMessage))).scalar_one() == 1
        with pytest.raises(HTTPException) as conflict:
            await community.post_message(fresh, ctx, cohort.cohort_uuid, 'Different content', 'one')
        assert conflict.value.status_code == 409


async def test_thread_rechecks_org_membership_verified_email_and_exact_program_access(db, community_setup):
    cohort, ctx, entitlement = community_setup
    foreign = TTContext(user=ctx.user, org_id=ctx.org_id+1)
    with pytest.raises(HTTPException) as tenant:
        await community.list_messages(db, foreign, cohort.cohort_uuid)
    assert tenant.value.status_code == 404
    staff = TTContext(user=SimpleNamespace(id=999, email_verified=True), org_id=ctx.org_id, is_staff=True, staff_roles=frozenset({'operator'}))
    assert (await community.list_messages(db, staff, cohort.cohort_uuid))['messages'] == []
    with pytest.raises(HTTPException):
        await community.post_message(db, staff, cohort.cohort_uuid, 'Cannot impersonate a learner', 'staff')
    entitlement.program_id += 100
    db.add(entitlement)
    await db.commit()
    with pytest.raises(HTTPException) as access:
        await community.list_messages(db, ctx, cohort.cohort_uuid)
    assert access.value.status_code == 403


async def test_private_active_session_blocks_posting_but_old_phase_does_not(db, community_setup):
    cohort, ctx, _ = community_setup
    earlier = await community.post_message(db, ctx, cohort.cohort_uuid, 'Earlier discussion must be hidden', 'before-private')
    now = datetime.now(timezone.utc)
    room = TTRoom(org_id=ctx.org_id, cohort_id=cohort.id, title='Independent work', starts_at=now-timedelta(minutes=10), duration_minutes=60, phase='individual_attempt', created_by=ctx.user.id)
    db.add(room)
    await db.commit()
    assert (await community.list_messages(db, ctx, cohort.cohort_uuid))['posting_blocked']
    hidden = await community.list_messages(db, ctx, cohort.cohort_uuid)
    assert hidden['can_read'] is False
    assert hidden['messages'] == []
    assert hidden['next_after'] == 0
    participant_staff = TTContext(user=ctx.user, org_id=ctx.org_id, is_staff=True, staff_roles=frozenset({'operator'}))
    assert (await community.list_messages(db, participant_staff, cohort.cohort_uuid))['messages'] == []
    oversight = TTContext(user=SimpleNamespace(id=999), org_id=ctx.org_id, is_staff=True, staff_roles=frozenset({'operator'}))
    assert (await community.list_messages(db, oversight, cohort.cohort_uuid))['messages'][0]['id'] == earlier['id']
    with pytest.raises(HTTPException) as blocked:
        await community.post_message(db, ctx, cohort.cohort_uuid, 'Do not help during independent work', 'private')
    assert blocked.value.status_code == 409
    room.starts_at = now-timedelta(days=2)
    db.add(room)
    await db.commit()
    assert (await community.post_message(db, ctx, cohort.cohort_uuid, 'Between sessions', 'after'))['text'] == 'Between sessions'


async def test_thread_pagination_and_validation_are_bounded(db, community_setup):
    cohort, ctx, _ = community_setup
    first = await community.post_message(db, ctx, cohort.cohort_uuid, 'First', 'one')
    second = await community.post_message(db, ctx, cohort.cohort_uuid, 'Second', 'two')
    page = await community.list_messages(db, ctx, cohort.cohort_uuid, limit=1)
    assert page['has_more'] is True
    assert page['messages'] == [first]
    assert (await community.list_messages(db, ctx, cohort.cohort_uuid, after=page['next_after']))['messages'] == [second]
    for text in (' ', 'x'*4001):
        with pytest.raises(HTTPException):
            await community.post_message(db, ctx, cohort.cohort_uuid, text, 'invalid')
    with pytest.raises(HTTPException):
        await community.list_messages(db, ctx, cohort.cohort_uuid, limit=101)


async def test_calendar_is_self_only_and_local_plan_dates_follow_dst(db, community_setup):
    cohort, ctx, _ = community_setup
    db.add(TTLearnerProfile(org_id=ctx.org_id, user_id=ctx.user.id, program_id=cohort.program_id, timezone='America/New_York'))
    db.add(TTPlan(org_id=ctx.org_id, user_id=ctx.user.id, program_id=cohort.program_id, plan_date='2026-11-01', version=1, budget_minutes=30, activities=[{'kind':'practice','minutes':10,'title':'Own activity'}]))
    db.add(TTPlan(org_id=ctx.org_id, user_id=ctx.user.id+1, program_id=cohort.program_id, plan_date='2026-11-01', budget_minutes=30, activities=[{'kind':'practice','minutes':10,'title':'Private other learner'}]))
    db.add(TTRoom(org_id=ctx.org_id, cohort_id=cohort.id, title='Own session', starts_at=datetime(2026,11,1,19,tzinfo=timezone.utc), created_by=ctx.user.id))
    await db.commit()
    start, end = datetime(2026,11,1,tzinfo=timezone.utc), datetime(2026,11,3,tzinfo=timezone.utc)
    calendar = await community.calendar_events(db, ctx, start, end)
    assert calendar['timezone'] == 'America/New_York'
    assert 'Private other learner' not in str(calendar)
    plan = next(e for e in calendar['events'] if e['kind'] == 'plan_activity')
    assert plan['starts_at'].startswith('2026-11-01T04:00')
    assert plan['ends_at'].startswith('2026-11-02T05:00')
    with pytest.raises(HTTPException):
        await community.calendar_events(db, ctx, start, start+timedelta(days=32))
    ics = community.calendar_ics(calendar)
    assert 'BEGIN:VCALENDAR' in ics
    assert 'VALUE=DATE:20261101' in ics
    assert 'Private other learner' not in ics


async def test_reminder_windows_are_paid_scoped_and_deduplicated(db, community_setup):
    cohort, ctx, entitlement = community_setup
    now = datetime(2026,11,15,10,tzinfo=timezone.utc)
    room = TTRoom(org_id=ctx.org_id, cohort_id=cohort.id, title='Tomorrow session', starts_at=now+timedelta(hours=20), created_by=ctx.user.id)
    db.add(room)
    await db.commit()
    # The clock is injected through the job payload only in this test's service call.
    await community.session_reminders_job(db, SimpleNamespace(org_id=ctx.org_id), now=now)
    await db.commit()
    await community.session_reminders_job(db, SimpleNamespace(org_id=ctx.org_id), now=now)
    await db.commit()
    rows = (await db.execute(select(TTNotification))).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == ctx.user.id
    assert rows[0].emailed is False
    await community.session_reminders_job(db, SimpleNamespace(org_id=ctx.org_id), now=now+timedelta(hours=19,minutes=30))
    await db.commit()
    assert (await db.execute(select(func.count()).select_from(TTNotification))).scalar_one() == 2
    entitlement.status = 'revoked'
    db.add(entitlement)
    db.add(TTRoom(org_id=ctx.org_id, cohort_id=cohort.id, title='No unpaid notification', starts_at=now+timedelta(minutes=20), created_by=ctx.user.id))
    await db.commit()
    await community.session_reminders_job(db, SimpleNamespace(org_id=ctx.org_id), now=now)
    await db.commit()
    assert (await db.execute(select(func.count()).select_from(TTNotification))).scalar_one() == 2

async def test_concurrent_retries_insert_one_record(db, engine, community_setup, tmp_path):
    import asyncio
    import sqlite3
    from sqlalchemy.ext.asyncio import create_async_engine
    cohort, ctx, _ = community_setup
    path = tmp_path/'community-concurrency.sqlite'
    destination = sqlite3.connect(str(path))
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        await raw.driver_connection.backup(destination)
    destination.close()
    isolated = create_async_engine('sqlite+aiosqlite:///'+str(path), connect_args={'timeout': 15})
    async def retry():
        async with AsyncSession(isolated, expire_on_commit=False) as session:
            return await community.post_message(session, ctx, cohort.cohort_uuid, 'One logical message', 'concurrent-key')
    try:
        first, second = await asyncio.gather(retry(), retry())
        assert first['message_uuid'] == second['message_uuid']
        async with AsyncSession(isolated) as session:
            assert (await session.execute(select(func.count()).select_from(TTCohortMessage))).scalar_one() == 1
    finally:
        await isolated.dispose()


async def test_revocation_and_email_changes_remove_thread_access(db, community_setup):
    cohort, ctx, _ = community_setup
    ctx.user.email_verified = False
    db.add(ctx.user)
    await db.commit()
    with pytest.raises(HTTPException) as unverified:
        await community.post_message(db, ctx, cohort.cohort_uuid, 'Unverified', 'email')
    assert unverified.value.status_code == 403
    ctx.user.email_verified = True
    db.add(ctx.user)
    member = (await db.execute(select(TTCohortMember).where(TTCohortMember.cohort_id == cohort.id))).scalar_one()
    member.status = 'revoked'
    db.add(member)
    await db.commit()
    with pytest.raises(HTTPException) as revoked:
        await community.list_messages(db, ctx, cohort.cohort_uuid)
    assert revoked.value.status_code == 403

async def test_http_routes_validate_cursors_and_export_private_ics(db, community_setup):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from src.core.events.database import get_db_session
    from src.routers.thinktogether.community import router
    from src.services.thinktogether.common import get_context
    cohort, ctx, _ = community_setup
    app = FastAPI()
    app.include_router(router, prefix='/api/v1/tt')
    app.dependency_overrides[get_context] = lambda: ctx
    async def session():
        yield db
    app.dependency_overrides[get_db_session] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        invalid = await client.get(f'/api/v1/tt/cohorts/{cohort.cohort_uuid}/messages?limit=101')
        assert invalid.status_code == 422
        posted = await client.post(f'/api/v1/tt/cohorts/{cohort.cohort_uuid}/messages', json={'text':'A routed message','idempotency_key':'http-key'})
        assert posted.status_code == 201
        response = await client.get(f'/api/v1/tt/cohorts/{cohort.cohort_uuid}/messages')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.json()['messages'][0]['text'] == 'A routed message'
        exported = await client.get('/api/v1/tt/calendar?format=ics')
        assert exported.status_code == 200
        assert exported.headers['cache-control'] == 'private, no-store'
        assert 'attachment' in exported.headers['content-disposition']
        assert exported.text.startswith('BEGIN:VCALENDAR\r\n')


def test_ics_escapes_content_and_keeps_instant_review_events_valid():
    exported = community.calendar_ics({'timezone':'UTC','events':[{
        'id':'review:stable', 'kind':'review', 'title':'Review\r\nBEGIN:VEVENT, unsafe;',
        'starts_at':'2026-11-15T10:00:00+00:00', 'ends_at':'2026-11-15T10:00:00+00:00',
        'all_day':False, 'href':'/thinktogether/practice?review=stable'}]})
    assert exported.count('BEGIN:VEVENT') == 2  # one structural line, one escaped title fragment
    assert '\r\nBEGIN:VEVENT, unsafe;' not in exported
    assert 'DTEND:' not in exported
