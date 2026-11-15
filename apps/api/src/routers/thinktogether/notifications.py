from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.thinktogether.core import TTNotification, TTNotificationPreference
from src.services.thinktogether.common import TTContext, get_context

router = APIRouter()


@router.get('/notifications')
async def notifications(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(TTNotification).where(TTNotification.org_id == ctx.org_id,
        TTNotification.user_id == ctx.user.id).order_by(TTNotification.id.desc()).limit(50))).scalars().all()
    return [{'id': r.id, 'title': r.title, 'href': r.href, 'read': r.read, 'created_at': r.created_at} for r in rows]


@router.post('/notifications/{identifier}/read')
async def read(identifier: int, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    row = await db.get(TTNotification, identifier)
    if not row or row.org_id != ctx.org_id or row.user_id != ctx.user.id:
        raise HTTPException(404, 'Notification not found')
    row.read = True
    db.add(row)
    await db.commit()
    return {'read': True}


class PreferenceInput(BaseModel):
    email_reminders: bool


@router.get('/notification-preferences')
async def preference(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    row = (await db.execute(select(TTNotificationPreference).where(TTNotificationPreference.org_id == ctx.org_id,
        TTNotificationPreference.user_id == ctx.user.id))).scalars().first()
    return {'email_reminders': bool(row and row.email_reminders)}


@router.put('/notification-preferences')
async def save_preference(body: PreferenceInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    row = (await db.execute(select(TTNotificationPreference).where(TTNotificationPreference.org_id == ctx.org_id,
        TTNotificationPreference.user_id == ctx.user.id))).scalars().first()
    row = row or TTNotificationPreference(org_id=ctx.org_id, user_id=ctx.user.id)
    row.email_reminders = body.email_reminders
    db.add(row)
    await db.commit()
    return {'email_reminders': row.email_reminders}
