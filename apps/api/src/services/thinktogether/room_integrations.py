"""Retryable media effects; durable application state remains authoritative."""
from sqlalchemy import select

from src.db.thinktogether.rooms import TTRoom
from src.db.users import User
from src.services.thinktogether import rooms
from src.services.thinktogether.ids import utcnow
from src.services.thinktogether.jobs import enqueue_job


async def enqueue_member_revocation(db, org_id, cohort_id, user_id, reason_key):
    found = (await db.execute(select(TTRoom).where(TTRoom.org_id == org_id, TTRoom.cohort_id == cohort_id))).scalars().all()
    for room in found:
        await enqueue_job(db, org_id, 'room_member_revoked', {'room_uuid': room.room_uuid, 'user_id': user_id},
                          f'media-revoke:{reason_key}:{room.room_uuid}:{user_id}')


async def revoke_media_member(db, job):
    room = (await db.execute(select(TTRoom).where(TTRoom.org_id == job.org_id, TTRoom.room_uuid == job.payload['room_uuid']))).scalar_one_or_none()
    user = await db.get(User, job.payload['user_id'])
    if room is None or user is None or not rooms.livekit_configured():
        return
    await rooms._livekit_rpc('RemoveParticipant', {'room': f'tt-{room.org_id}-{room.room_uuid}',
        'identity': rooms.participant_identity(room, user), 'revoke_token_ts': int(utcnow().timestamp()) + 1})


async def dispatch_room_agent(db, job):
    from src.services.thinktogether.facilitator import dispatch_facilitator
    room = (await db.execute(select(TTRoom).where(TTRoom.org_id == job.org_id, TTRoom.room_uuid == job.payload['room_uuid']).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if room is not None and room.phase == 'discussion' and room.agent_enabled:
        await dispatch_facilitator(room)


async def expire_commercial_access(db, job):
    """Run every scheduler tick; revoke paid media and board membership durably."""
    from src.db.thinktogether.billing import TTEntitlement
    from src.db.thinktogether.rooms import TTCohort, TTCohortMember
    from src.db.boards import BoardMember, Board
    from src.services.thinktogether.billing import has_active_access
    from sqlalchemy import delete
    expired = (await db.execute(select(TTEntitlement).where(TTEntitlement.org_id == job.org_id,
        TTEntitlement.status == 'active', TTEntitlement.expires_at <= utcnow()).with_for_update(skip_locked=True))).scalars().all()
    for grant in expired:
        grant.status = 'expired'
        db.add(grant)
        # Another active purchase/grant may extend the same program.
        if await has_active_access(db, job.org_id, grant.user_id, grant.program_id):
            continue
        members = (await db.execute(select(TTCohortMember).join(TTCohort, TTCohort.id == TTCohortMember.cohort_id).where(
            TTCohortMember.org_id == job.org_id, TTCohortMember.user_id == grant.user_id,
            TTCohortMember.status == 'active', TTCohort.program_id == grant.program_id))).scalars().all()
        for member in members:
            member.status = 'revoked'
            db.add(member)
            await enqueue_member_revocation(db, job.org_id, member.cohort_id, grant.user_id, f'expired:{grant.id}')
            board_ids = select(Board.id).join(TTRoom, TTRoom.board_uuid == Board.board_uuid).where(
                TTRoom.org_id == job.org_id, TTRoom.cohort_id == member.cohort_id)
            await db.execute(delete(BoardMember).where(BoardMember.user_id == grant.user_id, BoardMember.board_id.in_(board_ids)))
    await db.flush()
