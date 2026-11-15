from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_revocation_is_durable_idempotent_and_scoped(db, org, regular_user, monkeypatch):
    from src.db.thinktogether.rooms import TTCohort, TTRoom
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether import rooms
    from src.services.thinktogether.room_integrations import enqueue_member_revocation, revoke_media_member
    cohort = TTCohort(org_id=org.id, title='Cohort', language='en', timezone='UTC', schedule={})
    db.add(cohort)
    await db.flush()
    room = TTRoom(org_id=org.id, cohort_id=cohort.id, title='Discussion', phase='discussion', starts_at=datetime.now(timezone.utc), created_by=regular_user.id)
    db.add(room)
    await db.flush()
    await enqueue_member_revocation(db, org.id, cohort.id, regular_user.id, 'refund:one')
    await enqueue_member_revocation(db, org.id, cohort.id, regular_user.id, 'refund:one')
    await db.commit()
    jobs = (await db.execute(select(TTJob))).scalars().all()
    assert len(jobs) == 1
    calls = []
    async def rpc(method, payload):
        calls.append((method, payload))
        return {}
    monkeypatch.setattr(rooms, '_livekit_rpc', rpc)
    monkeypatch.setattr(rooms, 'livekit_configured', lambda: True)
    await revoke_media_member(db, jobs[0])
    assert calls[0][0] == 'RemoveParticipant'
    assert calls[0][1]['identity'] == rooms.participant_identity(room, regular_user)
    assert calls[0][1]['revoke_token_ts'] > datetime.now(timezone.utc).timestamp()
    calls.clear()
    await revoke_media_member(db, SimpleNamespace(org_id=org.id+1, payload=jobs[0].payload))
    assert calls == []
