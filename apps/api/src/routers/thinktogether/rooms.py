import asyncio
import json
import os
import secrets
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession
from src.core.events.database import get_db_session
from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom
from src.services.thinktogether.common import TTContext, get_context, require_permission
from src.services.thinktogether import rooms as service
from src.security.api_token_utils import get_authenticated_non_api_token_user

router = APIRouter(tags=['ThinkTogether rooms'])

class CohortInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    language: str = Field(min_length=2, max_length=20)
    timezone: str = Field(min_length=1, max_length=80)
    schedule: dict
    capacity: int = Field(default=6, ge=4, le=6)
    program_id: str | None = None
    exam_window_start: date | None = None
    exam_window_end: date | None = None

class MemberInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    status: Literal['invited', 'active', 'revoked'] = 'invited'

class SessionInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    local_date: date
    fold: int | None = Field(default=None, ge=0, le=1)
    case_item_id: str | None = None

class CommandInput(BaseModel):
    command_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    action: Literal['advance', 'chat', 'consent_audio', 'disable_agent', 'approve_board_summary', 'approve_agent_note']
    payload: dict = Field(default_factory=dict)

class UtteranceInput(BaseModel):
    utterance_id: str = Field(min_length=1, max_length=128)
    participant_identity: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=8000)

def check_internal(supplied, env):
    expected = os.getenv(env, '')
    if not expected:
        raise HTTPException(503, 'Internal integration is not configured')
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(403, 'Invalid internal credential')

@router.get('/cohorts')
async def cohorts(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.available_cohorts(db, ctx.org_id)

@router.post('/cohorts', status_code=201)
async def create_cohort(body: CohortInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    c = await service.create_cohort(db, ctx, body)
    return {'id': c.cohort_uuid, 'title': c.title, 'status': c.status, 'schedule': c.schedule, 'timezone': c.timezone, 'language': c.language, 'capacity': c.capacity}

@router.get('/cohorts/manage')
async def manage_cohorts(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, 'groups')
    rows = (await db.execute(select(TTCohort).where(TTCohort.org_id == ctx.org_id).order_by(TTCohort.id))).scalars().all()
    return [{'id': c.cohort_uuid, 'title': c.title, 'status': c.status, 'language': c.language, 'timezone': c.timezone, 'schedule': c.schedule, 'capacity': c.capacity} for c in rows]

@router.post('/cohorts/{cohort_uuid}/members')
async def member(cohort_uuid: str, body: MemberInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.manage_member(db, ctx, cohort_uuid, body.user_id, body.status)

@router.post('/cohorts/{cohort_uuid}/sessions', status_code=201)
async def session(cohort_uuid: str, body: SessionInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.create_session(db, ctx, cohort_uuid, body)

@router.get('/rooms')
async def rooms(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    stmt = select(TTRoom).where(TTRoom.org_id == ctx.org_id)
    if not ctx.can('groups'):
        stmt = stmt.join(TTCohortMember, (TTCohortMember.cohort_id == TTRoom.cohort_id) & (TTCohortMember.org_id == ctx.org_id)).where(TTCohortMember.user_id == ctx.user.id, TTCohortMember.status == 'active')
    found = (await db.execute(stmt.order_by(TTRoom.starts_at).limit(100))).scalars().all()
    result = []
    for item in found:
        try:
            result.append(await service.room_view(db, ctx, item.room_uuid))
        except HTTPException as error:
            if error.status_code != 403: raise
    return result

@router.get('/rooms/{room_uuid}')
async def room(room_uuid: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.room_view(db, ctx, room_uuid)

@router.post('/rooms/{room_uuid}/commands')
async def command(room_uuid: str, body: CommandInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.apply_command(db, ctx, room_uuid, body.command_id, body.expected_revision, body.action, body.payload)

@router.post('/rooms/{room_uuid}/media-token')
async def media(room_uuid: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.media_token(db, ctx, room_uuid)

@router.get('/rooms/{room_uuid}/task')
async def personal_task(room_uuid: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    from src.services.thinktogether.room_learning import room_task
    return await room_task(db, ctx, room_uuid)

@router.get('/rooms/{room_uuid}/events')
async def events(room_uuid: str, request: Request, after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None), ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    if last_event_id is not None:
        try: after = max(after, int(last_event_id))
        except ValueError: raise HTTPException(422, 'Last-Event-ID must be an integer')
    await service.room_view(db, ctx, room_uuid)
    async def stream():
        cursor = after
        while not await request.is_disconnected():
            try:
                current_ctx = await get_context(ctx.user, db)
                batch = await service.event_batch(db, current_ctx, room_uuid, cursor)
            except HTTPException:
                yield 'event: access_revoked\ndata: {}\n\n'
                break
            await db.rollback()
            for event in batch:
                cursor = event['id']
                yield f"id: {cursor}\nevent: room_event\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            if not batch: yield ': heartbeat\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})

@router.get('/internal/boards/{board_uuid}/access')
async def internal_board(board_uuid: str, x_internal_key: str | None = Header(default=None), user = Depends(get_authenticated_non_api_token_user), db: AsyncSession = Depends(get_db_session)):
    check_internal(x_internal_key, 'COLLAB_INTERNAL_KEY')
    managed = (await db.execute(select(TTRoom.id).where(TTRoom.board_uuid == board_uuid))).scalar_one_or_none()
    if managed is None:
        return {'managed': False, 'can_write': True}
    ctx = await get_context(user, db)
    return await service.board_access(db, ctx, board_uuid)

@router.get('/internal/rooms/{room_uuid}/context')
async def internal_voice_context(room_uuid: str, x_tt_internal_key: str | None = Header(default=None), db: AsyncSession = Depends(get_db_session)):
    check_internal(x_tt_internal_key, 'TT_INTERNAL_KEY')
    return await service.voice_context(db, room_uuid)

@router.post('/internal/rooms/{room_uuid}/utterances')
async def internal_utterance(room_uuid: str, body: UtteranceInput, x_tt_internal_key: str | None = Header(default=None), db: AsyncSession = Depends(get_db_session)):
    check_internal(x_tt_internal_key, 'TT_INTERNAL_KEY')
    return await service.record_utterance(db, room_uuid, body.utterance_id, body.participant_identity, body.text)
