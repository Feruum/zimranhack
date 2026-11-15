"""Moderator-agreed AI notes: exact stored text, room lock and durable outbox."""
import os

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from src.db.thinktogether.rooms import TTRoom, TTRoomEvent


async def approve_proposal(db, room, ctx, payload):
    if not ctx.can('groups'):
        raise HTTPException(403, 'Group operator permission required')
    if room.phase != 'discussion':
        raise HTTPException(409, 'Board notes can only be agreed during discussion')
    if set(payload) != {'event_sequence'} or type(payload.get('event_sequence')) is not int or payload['event_sequence'] <= 0:
        raise HTTPException(422, 'Approve an existing proposal event; replacement text is not accepted')
    sequence = payload['event_sequence']
    event = await _proposal(db, room, sequence)
    # Multiple command IDs for the same proposal remain one durable effect.
    from src.services.thinktogether.jobs import enqueue_job
    await enqueue_job(db, room.org_id, 'board.note', {'room_uuid': room.room_uuid, 'event_sequence': sequence},
                      f'board-note:{room.room_uuid}:{sequence}')
    return {'event_sequence': event.sequence, 'note_id': f'tt-note:{room.room_uuid}:{sequence}'}


async def _proposal(db, room, sequence):
    event = (await db.execute(select(TTRoomEvent).where(TTRoomEvent.org_id == room.org_id,
        TTRoomEvent.room_id == room.id, TTRoomEvent.sequence == sequence, TTRoomEvent.kind == 'agent_message'))).scalar_one_or_none()
    if event is None or event.payload.get('facilitation_action') != 'propose_note':
        raise HTTPException(422, 'Choose an AI board-note proposal in this room')
    text = event.payload.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 700:
        raise HTTPException(422, 'Invalid board-note proposal')
    return event


async def write_board_note(db, job):
    """Outbox handler. Caller commits/retries; no mutation after discussion.

    Keep the room row lock through collab RPC so phase changes cannot race a
    note write. Collab acknowledges only after synchronous durable persistence.
    """
    room = (await db.execute(select(TTRoom).where(TTRoom.room_uuid == job.payload.get('room_uuid'),
        TTRoom.org_id == job.org_id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if room is None:
        raise HTTPException(404, 'Room not found')
    if room.phase != 'discussion':
        return
    sequence = job.payload.get('event_sequence')
    if type(sequence) is not int or sequence <= 0:
        raise HTTPException(422, 'Invalid proposal reference')
    event = await _proposal(db, room, sequence)
    approvals = (await db.execute(select(TTRoomEvent).where(TTRoomEvent.org_id == room.org_id,
        TTRoomEvent.room_id == room.id, TTRoomEvent.kind == 'approve_agent_note', TTRoomEvent.actor_id.is_not(None)))).scalars().all()
    if not any(a.payload.get('event_sequence') == sequence for a in approvals):
        raise HTTPException(403, 'An explicit operator agreement is required before writing a note')
    if not room.board_uuid:
        raise HTTPException(409, 'Room has no shared board')
    secret = os.getenv('COLLAB_INTERNAL_KEY', '')
    if not secret:
        raise HTTPException(503, 'Shared board integration is not configured')
    base = os.getenv('TT_COLLAB_URL', 'http://collab:4000').rstrip('/')
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.post(f'{base}/internal/tt/board-notes', headers={'X-Internal-Key': secret},
            json={'room_uuid': room.room_uuid, 'board_uuid': room.board_uuid, 'event_sequence': sequence,
                  'note_id': f'tt-note:{room.room_uuid}:{sequence}', 'text': event.payload['text']})
        response.raise_for_status()
        if response.json().get('written') is not True:
            raise HTTPException(503, 'Shared board did not acknowledge the agreed note')
