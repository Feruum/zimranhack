"""Learner support conversations and a permission-gated staff queue."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.services.thinktogether import support as service
from src.services.thinktogether.common import TTContext, get_context

router = APIRouter(tags=['ThinkTogether support'])
Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    idempotency_key: Key
    message: Message


class TicketInput(MessageInput):
    kind: Literal['learning', 'group', 'billing', 'assessment']
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    attempt_id: UUID | None = None


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    idempotency_key: Key
    message: Message | None = None


class StatusInput(ResolveInput):
    status: Literal['open', 'in_progress', 'resolved']


@router.get('/support/tickets')
async def tickets(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.list_tickets(db, ctx)


@router.post('/support/tickets')
async def create(body: TicketInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.create_ticket(db, ctx, **body.model_dump(mode='json'))


@router.post('/support/tickets/{identifier}/messages')
async def reply(identifier: UUID, body: MessageInput, ctx: TTContext = Depends(get_context),
                db: AsyncSession = Depends(get_db_session)):
    return await service.add_message(db, ctx, str(identifier), **body.model_dump())


@router.get('/staff/support')
async def staff_queue(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.list_tickets(db, ctx, staff=True)


@router.patch('/staff/support/{identifier}')
async def status(identifier: UUID, body: StatusInput, ctx: TTContext = Depends(get_context),
                 db: AsyncSession = Depends(get_db_session)):
    return await service.set_status(db, ctx, str(identifier), **body.model_dump())


@router.post('/staff/support/{identifier}/resolve')
async def resolve(identifier: UUID, body: ResolveInput, ctx: TTContext = Depends(get_context),
                  db: AsyncSession = Depends(get_db_session)):
    return await service.set_status(db, ctx, str(identifier), status='resolved', **body.model_dump())
