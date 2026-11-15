"""Billing-authorized AI operational aggregates, never learning evidence."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.services.thinktogether.ai_usage import usage_summary
from src.services.thinktogether.common import TTContext, get_context, utcnow

router = APIRouter(tags=['ThinkTogether usage'])


@router.get('/staff/usage')
async def staff_usage(start: datetime | None = None, end: datetime | None = None,
                      ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    end = end or utcnow()
    start = start or end-timedelta(days=7)
    return await usage_summary(db, ctx, start=start, end=end)
