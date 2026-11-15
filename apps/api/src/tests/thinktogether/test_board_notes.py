from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.thinktogether.rooms import TTCohort, TTRoom, TTRoomEvent, TTRoomCommand
from src.db.thinktogether.jobs import TTJob
from src.services.thinktogether import board_notes, rooms
from src.services.thinktogether.jobs import run_job


@pytest.fixture
async def proposed_note(db, org, regular_user):
    cohort = TTCohort(org_id=org.id, title='Cohort', language='en', timezone='UTC', schedule={})
    db.add(cohort)
    await db.flush()
    room = TTRoom(org_id=org.id, cohort_id=cohort.id, title='Session', phase='discussion', revision=1,
                  starts_at=datetime.now(timezone.utc), created_by=regular_user.id, board_uuid='board_87654321-4321-4321-4321-cba987654321')
    db.add(room)
    await db.flush()
    db.add(TTRoomEvent(org_id=org.id, room_id=room.id, sequence=1, kind='agent_message', payload={'text': 'The group agreed to compare two hypotheses.', 'source': 'facilitator', 'facilitation_action': 'propose_note'}))
    await db.commit()
    ctx = SimpleNamespace(org_id=org.id, user=regular_user, can=lambda permission: permission == 'groups')
    return room, ctx


async def test_approval_references_saved_proposal_and_enqueues_once_with_cas(db, proposed_note):
    room, ctx = proposed_note
    first = await rooms.apply_command(db, ctx, room.room_uuid, 'approve-1', 1, 'approve_agent_note', {'event_sequence': 1})
    assert first == await rooms.apply_command(db, ctx, room.room_uuid, 'approve-1', 1, 'approve_agent_note', {'event_sequence': 1})
    job = (await db.execute(select(TTJob).where(TTJob.kind == 'board.note'))).scalar_one()
    assert job.payload == {'room_uuid': room.room_uuid, 'event_sequence': 1}
    assert first['revision'] == 2
    with pytest.raises(HTTPException):
        await rooms.apply_command(db, ctx, room.room_uuid, 'approve-2', 2, 'approve_agent_note', {'event_sequence': 1, 'text': 'client replacement'})


@pytest.mark.parametrize('reason', ['permission', 'phase', 'wrong_event', 'wrong_room'])
async def test_board_approval_requires_operator_discussion_and_same_room_proposal(db, proposed_note, monkeypatch, reason):
    room, ctx = proposed_note
    if reason == 'permission': ctx.can = lambda p: False
    if reason == 'phase': room.phase = 'transfer'; db.add(room); await db.commit()
    if reason == 'wrong_event':
        event = (await db.execute(select(TTRoomEvent))).scalar_one()
        event.payload = {'text': 'ordinary chat'}; db.add(event); await db.commit()
    if reason == 'wrong_room':
        event = (await db.execute(select(TTRoomEvent))).scalar_one()
        event.org_id += 100; db.add(event); await db.commit()
    async def allowed(*args, **kwargs): pass
    monkeypatch.setattr(rooms, 'require_cohort_member', allowed)
    monkeypatch.setattr(rooms, 'require_paid', allowed)
    with pytest.raises(HTTPException):
        await rooms.apply_command(db, ctx, room.room_uuid, 'bad-approval', 1, 'approve_agent_note', {'event_sequence': 1})
    assert (await db.execute(select(TTJob))).scalars().all() == []


async def test_worker_resolves_text_from_db_and_network_failure_keeps_durable_outbox(db, proposed_note, monkeypatch):
    room, ctx = proposed_note
    monkeypatch.setenv('COLLAB_INTERNAL_KEY', 'collab-secret')
    await rooms.apply_command(db, ctx, room.room_uuid, 'approve-1', 1, 'approve_agent_note', {'event_sequence': 1})
    job = (await db.execute(select(TTJob))).scalar_one()
    job_id = job.id
    async def unavailable(request): raise httpx.ConnectError('offline', request=request)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(unavailable), **kwargs))
    assert await run_job(db, job_id, {'board.note': board_notes.write_board_note}) is False
    await db.refresh(job)
    assert job.status == 'retry'
    assert (await db.execute(select(TTRoomCommand))).scalars().first() is not None


async def test_worker_stops_after_phase_advance_and_requires_approval_marker(db, proposed_note, monkeypatch):
    room, ctx = proposed_note
    job = TTJob(org_id=room.org_id, kind='board.note', payload={'room_uuid': room.room_uuid, 'event_sequence': 1}, idempotency_key='fake')
    with pytest.raises(HTTPException): await board_notes.write_board_note(db, job)
    await rooms.apply_command(db, ctx, room.room_uuid, 'approve-1', 1, 'approve_agent_note', {'event_sequence': 1})
    room.phase = 'summary'; db.add(room); await db.commit()
    with monkeypatch.context() as patched:
        patched.setattr(httpx, 'AsyncClient', lambda **kwargs: (_ for _ in ()).throw(AssertionError('No board writes after phase change')))
        await board_notes.write_board_note(db, job)


async def test_worker_posts_deterministic_native_note_contract_without_client_text(db, proposed_note, monkeypatch):
    import json
    room, ctx = proposed_note
    monkeypatch.setenv('COLLAB_INTERNAL_KEY', 'collab-secret')
    await rooms.apply_command(db, ctx, room.room_uuid, 'approve-1', 1, 'approve_agent_note', {'event_sequence': 1})
    job = (await db.execute(select(TTJob))).scalar_one()
    calls = []
    async def send(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body['text'] == 'The group agreed to compare two hypotheses.'
        assert body['note_id'] == f'tt-note:{room.room_uuid}:1'
        assert body['board_uuid'] == room.board_uuid
        assert request.headers['X-Internal-Key'] == 'collab-secret'
        return httpx.Response(200, json={'written': True})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(send), **kwargs))
    await board_notes.write_board_note(db, job)
    await board_notes.write_board_note(db, job)
    assert len(calls) == 2
