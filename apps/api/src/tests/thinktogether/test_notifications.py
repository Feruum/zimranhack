import pytest
from sqlmodel import select

from src.db.thinktogether.core import TTNotification
from src.services.thinktogether.notifications import notify_user


@pytest.mark.asyncio
async def test_notification_replay_does_not_duplicate(db, org, regular_user):
    first = await notify_user(db, org.id, regular_user.id, 'review:1', 'Time to revisit a skill', '/thinktogether/practice')
    await db.commit()
    second = await notify_user(db, org.id, regular_user.id, 'review:1', 'Time to revisit a skill', '/thinktogether/practice')
    await db.commit()
    assert first.id == second.id
    records = (await db.execute(select(TTNotification).where(TTNotification.user_id == regular_user.id))).scalars().all()
    assert len(records) == 1
