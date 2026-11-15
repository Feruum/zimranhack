"""Long-lived streams must reauthorize after transaction expiration."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.jobs import TTJob
from src.db.organization_config import OrganizationConfig
from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom, TTRoomEvent
from src.db.user_organizations import UserOrganization
from src.db.users import User
from src.security.org_auth import get_user_org_rows, holds_admin_or_maintainer_role
from src.services.thinktogether.common import get_context


@pytest.fixture
def context_org(monkeypatch):
    monkeypatch.setenv('TT_ORG_SLUG', 'test-org')


async def test_context_reloads_expired_membership_after_rollback(db, org, admin_user, context_org):
    assert (await get_context(admin_user, db)).can('groups')
    await db.rollback()
    assert (await get_context(admin_user, db)).can('groups')


@pytest.mark.parametrize('boundary', ['rollback', 'commit'])
async def test_membership_cache_revalidates_revocation_across_transactions(
    db, engine, org, regular_user, context_org, boundary,
):
    org_id = org.id
    assert await get_user_org_rows(regular_user.id, org_id, db)
    await getattr(db, boundary)()
    async with AsyncSession(engine) as writer:
        await writer.execute(delete(UserOrganization).where(UserOrganization.user_id == regular_user.id))
        await writer.commit()
    with pytest.raises(HTTPException) as denied:
        await get_context(regular_user, db)
    assert denied.value.status_code == 403


@pytest.mark.parametrize('boundary', ['rollback', 'commit'])
async def test_membership_cache_revalidates_role_change_across_transactions(
    db, engine, org, admin_user, user_role, boundary,
):
    org_id = org.id
    assert await holds_admin_or_maintainer_role(admin_user.id, org_id, db)
    await getattr(db, boundary)()
    async with AsyncSession(engine) as writer:
        await writer.execute(update(UserOrganization).where(
            UserOrganization.user_id == admin_user.id).values(role_id=4))
        await writer.commit()
    assert not await holds_admin_or_maintainer_role(admin_user.id, org_id, db)


@pytest.mark.parametrize('boundary', ['rollback', 'commit'])
async def test_context_rechecks_former_superadmin_across_transactions(
    db, engine, org, admin_user, context_org, boundary,
):
    await db.execute(update(User).where(User.id == admin_user.id).values(is_superadmin=True))
    await db.commit()
    assert (await get_context(admin_user, db)).can('groups')
    await getattr(db, boundary)()
    async with AsyncSession(engine) as writer:
        await writer.execute(update(User).where(User.id == admin_user.id).values(is_superadmin=False))
        await writer.execute(delete(UserOrganization).where(UserOrganization.user_id == admin_user.id))
        await writer.commit()
    with pytest.raises(HTTPException) as denied:
        await get_context(admin_user, db)
    assert denied.value.status_code == 403


async def test_context_rechecks_auth_policy_after_rollback(
    db, engine, org, regular_user, context_org, monkeypatch,
):
    from src.security.session_context import SessionProvenance
    from src.services.orgs import auth_policy
    org_id = org.id
    monkeypatch.setattr(auth_policy, 'get_session_provenance',
                        lambda: SessionProvenance(amr='password', org_id=org_id))
    db.add(OrganizationConfig(org_id=org_id, config={'admin_toggles': {'security': {}}}))
    await db.commit()
    await get_context(regular_user, db)
    await db.rollback()
    async with AsyncSession(engine) as writer:
        await writer.execute(update(OrganizationConfig).where(OrganizationConfig.org_id == org_id).values(
            config={'admin_toggles': {'security': {'allowed_auth_methods': ['sso']}}}))
        await writer.commit()
    with pytest.raises(HTTPException) as denied:
        await get_context(regular_user, db)
    assert denied.value.status_code == 403


async def test_context_preserves_pending_config_edits_in_current_transaction(
    db, org, regular_user, context_org,
):
    from src.services.orgs.org_config_lookup import get_session_org_config
    db.add(OrganizationConfig(org_id=org.id, config={'admin_toggles': {'security': {}}}))
    await db.commit()
    config = await get_session_org_config(db, org.id)
    config.config = {**config.config, 'pending_editor_change': 'keep me'}
    await get_context(regular_user, db)
    assert config.config['pending_editor_change'] == 'keep me'


@pytest.fixture
async def stream_room(db, org, regular_user, monkeypatch, context_org):
    from src.services.thinktogether import rooms
    monkeypatch.setattr(rooms, 'require_paid', AsyncMock())
    await db.execute(update(User).where(User.id == regular_user.id).values(email_verified=True))
    regular_user.email_verified = True
    cohort = TTCohort(org_id=org.id, title='SSE regression', language='en', timezone='UTC', schedule={}, status='active')
    db.add(cohort)
    await db.flush()
    db.add(TTCohortMember(org_id=org.id, cohort_id=cohort.id, user_id=regular_user.id, status='active'))
    room = TTRoom(org_id=org.id, cohort_id=cohort.id, title='Persistent stream',
                  starts_at=datetime.now(timezone.utc), created_by=regular_user.id, phase='discussion')
    db.add(room)
    await db.commit()
    return room.room_uuid, room.id, org.id


async def test_room_stream_survives_polls_delivers_later_events_replays_and_revokes(
    db, engine, regular_user, stream_room, monkeypatch,
):
    from src.routers.thinktogether import rooms as router
    monkeypatch.setattr(router, 'asyncio', SimpleNamespace(sleep=AsyncMock()))
    room_uuid, room_id, org_id = stream_room
    ctx = await get_context(regular_user, db)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    response = await router.events(room_uuid, request, after=0, last_event_id=None, ctx=ctx, db=db)
    stream = response.body_iterator
    try:
        for _ in range(3):
            assert await anext(stream) == ': heartbeat\n\n'
        async with AsyncSession(engine) as writer:
            writer.add_all([
                TTRoomEvent(org_id=org_id, room_id=room_id, sequence=1, kind='chat',
                            actor_id=regular_user.id, payload={'text': 'Arrived after polling'}),
                TTRoomEvent(org_id=org_id, room_id=room_id, sequence=2, kind='advance',
                            actor_id=regular_user.id, payload={'phase': 'transfer'}),
            ])
            await writer.commit()
        assert 'Arrived after polling' in await anext(stream)
        assert '"phase":"transfer"' in await anext(stream)
    finally:
        await stream.aclose()

    replay = await router.events(room_uuid, request, after=0, last_event_id='1', ctx=ctx, db=db)
    stream = replay.body_iterator
    try:
        chunk = await anext(stream)
        assert chunk.startswith('id: 2\n') and '"kind":"advance"' in chunk
        async with AsyncSession(engine) as writer:
            await writer.execute(delete(UserOrganization).where(UserOrganization.user_id == regular_user.id))
            await writer.commit()
        assert await anext(stream) == 'event: access_revoked\ndata: {}\n\n'
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()


async def test_generation_stream_survives_rollback_and_observes_staff_role_revocation(
    db, engine, org, admin_user, user_role, context_org, monkeypatch,
):
    from src.routers.thinktogether import content_generation as router
    from src.services.thinktogether.content_generation import KIND
    monkeypatch.setattr(router, 'asyncio', SimpleNamespace(sleep=AsyncMock()))
    db.add(TTJob(org_id=org.id, kind=KIND, idempotency_key='sse-generation',
                 job_uuid='sse-generation', payload={'stage': 'outline', 'model': 'synthetic-test-model',
                                                     'request': {'title': 'SSE regression'}}))
    await db.commit()
    ctx = await get_context(admin_user, db)
    response = await router.events('sse-generation', SimpleNamespace(is_disconnected=AsyncMock(return_value=False)), ctx=ctx, db=db)
    stream = response.body_iterator
    try:
        assert 'event: progress' in await anext(stream)
        assert await anext(stream) == ': heartbeat\n\n'
        async with AsyncSession(engine) as writer:
            await writer.execute(update(UserOrganization).where(
                UserOrganization.user_id == admin_user.id).values(role_id=4))
            await writer.commit()
        assert await anext(stream) == 'event: access_revoked\ndata: {}\n\n'
    finally:
        await stream.aclose()
