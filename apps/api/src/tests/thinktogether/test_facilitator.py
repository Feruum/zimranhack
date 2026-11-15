from datetime import datetime, timezone
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom, TTRoomConsent, TTVoiceUtterance, TTRoomEvent
from src.db.thinktogether.facilitator import TTFacilitationTurn
from src.services.thinktogether import facilitator, rooms


@pytest.fixture
async def conversation(db, org, regular_user, monkeypatch):
    from src.db.users import User
    user = await db.get(User, regular_user.id)
    user.email_verified = True
    db.add(user)
    cohort = TTCohort(org_id=org.id, title='Cohort', language='en', timezone='UTC', schedule={}, status='active')
    db.add(cohort)
    await db.flush()
    db.add(TTCohortMember(org_id=org.id, cohort_id=cohort.id, user_id=user.id, status='active'))
    room = TTRoom(org_id=org.id, cohort_id=cohort.id, title='Discussion', phase='discussion', starts_at=datetime.now(timezone.utc), created_by=user.id)
    db.add(room)
    await db.flush()
    db.add(TTRoomConsent(org_id=org.id, room_id=room.id, user_id=user.id, audio_processing=True))
    db.add(TTVoiceUtterance(org_id=org.id, room_id=room.id, user_id=user.id, utterance_id='turn-1', text='What evidence supports this approach?'))
    await db.commit()
    async def paid(*args, **kwargs): pass
    monkeypatch.setattr(rooms, 'require_paid', paid)
    return room, user


async def test_real_provider_boundary_is_structured_bounded_and_idempotent(db, conversation, monkeypatch):
    room, user = conversation
    room_uuid, identity = room.room_uuid, rooms.participant_identity(room, user)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        assert kwargs['output_type'] is facilitator.Facilitation
        data = json.loads(kwargs['user_prompt'])
        assert data['utterance']['speaker'] == identity
        assert set(data) == {'case', 'board_summary', 'participants', 'recent_chat', 'recent_voice', 'utterance', 'language'}
        assert kwargs['max_tokens'] <= 350
        return facilitator.Facilitation(action='clarify', text='Which piece of evidence would distinguish the two approaches?')
    monkeypatch.setattr(facilitator, 'generate', generate)
    result = await facilitator.facilitate(db, room_uuid, 'turn-1')
    assert result == await facilitator.facilitate(db, room_uuid, 'turn-1')
    assert len(calls) == 1
    assert result['revision'] == 1
    events = (await db.execute(select(TTRoomEvent))).scalars().all()
    assert len(events) == 1 and events[0].payload['text'] == result['text']
    stored = (await db.execute(select(TTFacilitationTurn))).scalar_one()
    assert stored.model_version and stored.prompt_version


async def test_unconfigured_provider_returns_503_and_publishes_nothing(db, conversation, monkeypatch):
    room, _ = conversation
    async def unavailable(**kwargs): raise RuntimeError('missing provider key')
    monkeypatch.setattr(facilitator, 'generate', unavailable)
    with pytest.raises(HTTPException) as error:
        await facilitator.facilitate(db, room.room_uuid, 'turn-1')
    assert error.value.status_code == 503
    assert (await db.execute(select(TTRoomEvent))).scalars().all() == []


@pytest.mark.parametrize('change', ['phase', 'consent', 'disable'])
async def test_context_is_revalidated_after_reasoning_before_publication(db, conversation, monkeypatch, change):
    room, user = conversation
    user_id = user.id
    async def changed(**kwargs):
        if change == 'phase': room.phase = 'transfer'; db.add(room)
        elif change == 'disable': room.agent_enabled = False; db.add(room)
        else:
            consent = (await db.execute(select(TTRoomConsent).where(TTRoomConsent.user_id == user_id))).scalar_one()
            consent.audio_processing = False
            db.add(consent)
        await db.commit()
        return facilitator.Facilitation(action='clarify', text='Must remain unpublished?')
    monkeypatch.setattr(facilitator, 'generate', changed)
    with pytest.raises(HTTPException) as error:
        await facilitator.facilitate(db, room.room_uuid, 'turn-1')
    assert error.value.status_code in (403, 409)
    assert (await db.execute(select(TTRoomEvent))).scalars().all() == []


async def test_wait_decision_is_durable_without_synthesized_reply(db, conversation, monkeypatch):
    room, _ = conversation
    async def wait(**kwargs): return facilitator.Facilitation(action='wait', text='')
    monkeypatch.setattr(facilitator, 'generate', wait)
    first = await facilitator.facilitate(db, room.room_uuid, 'turn-1')
    assert first['text'] == ''
    assert first == await facilitator.facilitate(db, room.room_uuid, 'turn-1')
    assert (await db.execute(select(TTRoomEvent))).scalars().all() == []


def test_model_response_validation_rejects_tools_urls_and_empty_questions():
    for payload in [dict(action='grade', text='pass'), dict(action='clarify', text='https://example.com'), dict(action='clarify', text=''), dict(action='wait', text='Speak anyway')]:
        with pytest.raises(ValueError): facilitator.Facilitation(**payload)


async def test_database_lease_serializes_across_api_requests_without_holding_phase_lock(db, conversation, monkeypatch):
    import time
    from src.db.thinktogether.facilitator import TTFacilitatorState
    room, _ = conversation
    room_uuid = room.room_uuid
    lease = TTFacilitatorState(org_id=room.org_id, room_id=room.id, lease_token='other-api-replica', lease_until=time.time() + 80)
    db.add(lease)
    await db.commit()
    async def forbidden(**kwargs): raise AssertionError('Concurrent model call must not happen')
    monkeypatch.setattr(facilitator, 'generate', forbidden)
    with pytest.raises(HTTPException) as busy:
        await facilitator.facilitate(db, room_uuid, 'turn-1')
    assert busy.value.status_code == 409
    assert busy.value.headers['Retry-After'] == '1'


async def test_failed_generation_releases_lease_for_safe_same_id_retry(db, conversation, monkeypatch):
    room, _ = conversation
    room_uuid = room.room_uuid
    async def unavailable(**kwargs): raise RuntimeError('offline')
    monkeypatch.setattr(facilitator, 'generate', unavailable)
    with pytest.raises(HTTPException): await facilitator.facilitate(db, room_uuid, 'turn-1')
    async def recovered(**kwargs): return facilitator.Facilitation(action='reflect', text='The group has identified two distinct assumptions.')
    monkeypatch.setattr(facilitator, 'generate', recovered)
    result = await facilitator.facilitate(db, room_uuid, 'turn-1')
    assert result['revision'] == 1
    assert len((await db.execute(select(TTRoomEvent))).scalars().all()) == 1


@pytest.mark.parametrize('camel_case', [False, True])
async def test_dispatch_is_named_room_bound_and_idempotent(conversation, monkeypatch, camel_case):
    import httpx
    room, _ = conversation
    monkeypatch.setenv('LIVEKIT_URL', 'wss://project.livekit.cloud')
    monkeypatch.setenv('LIVEKIT_API_KEY', 'key')
    monkeypatch.setenv('LIVEKIT_API_SECRET', 'secret-value-for-local-tests-at-least-32')
    monkeypatch.setenv('TT_INTERNAL_KEY', 'key')
    calls, dispatches = [], []
    async def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body['room'] == f'tt-{room.org_id}-{room.room_uuid}'
        if request.url.path.endswith('/ListDispatch'):
            return httpx.Response(200, json={'agentDispatches' if camel_case else 'agent_dispatches': dispatches})
        assert body['agent_name'] == 'thinktogether-facilitator'
        assert json.loads(body['metadata']) == {'room_uuid': room.room_uuid}
        dispatches.append({'agentName' if camel_case else 'agent_name': body['agent_name']})
        return httpx.Response(200, json={'id': 'dispatch-1'})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    await facilitator.dispatch_facilitator(room)
    await facilitator.dispatch_facilitator(room)
    assert sum(r.url.path.endswith('/CreateDispatch') for r in calls) == 1
