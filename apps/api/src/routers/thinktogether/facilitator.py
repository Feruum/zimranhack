from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel.ext.asyncio.session import AsyncSession
from src.core.events.database import get_db_session
from src.routers.thinktogether.rooms import check_internal
from src.services.thinktogether.facilitator import facilitate

router = APIRouter(tags=['ThinkTogether facilitator'])


class FacilitationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    utterance_id: str = Field(min_length=1, max_length=128)


@router.post('/internal/rooms/{room_uuid}/facilitate')
async def internal_facilitate(room_uuid: str, body: FacilitationInput,
        x_tt_internal_key: str | None = Header(default=None), db: AsyncSession = Depends(get_db_session)):
    check_internal(x_tt_internal_key, 'TT_INTERNAL_KEY')
    return await facilitate(db, room_uuid, body.utterance_id)
