from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession
from src.core.events.database import get_db_session
from src.services.thinktogether.common import TTContext, get_context
from src.services.thinktogether import community

router = APIRouter(tags=['ThinkTogether community'])


class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=128)


@router.get('/cohorts/{cohort_uuid}/messages')
async def messages(cohort_uuid: str, response: Response, after: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100), ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    response.headers['Cache-Control'] = 'private, no-store'
    return await community.list_messages(db, ctx, cohort_uuid, after, limit)


@router.post('/cohorts/{cohort_uuid}/messages', status_code=201)
async def post_message(cohort_uuid: str, body: MessageInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await community.post_message(db, ctx, cohort_uuid, body.text, body.idempotency_key)


@router.get('/calendar')
async def calendar(response: Response, start: datetime | None = None, end: datetime | None = None, format: Literal['json', 'ics'] = 'json', ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    response.headers['Cache-Control'] = 'private, no-store'
    result = await community.calendar_events(db, ctx, start, end)
    if format == 'ics':
        return Response(community.calendar_ics(result), media_type='text/calendar; charset=utf-8', headers={
            'Content-Disposition': 'attachment; filename="thinktogether-calendar.ics"', 'Cache-Control': 'private, no-store'})
    return result
