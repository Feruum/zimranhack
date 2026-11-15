"""Private room tasks share immutable attempts with the individual learning loop."""
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select

from src.db.thinktogether.learning import TTDelivery, TTItem, TTAttempt
from src.db.thinktogether.rooms import TTCohort, TTRoom
from src.services.thinktogether import rooms, learning
from src.services.thinktogether.ids import utcnow, as_utc
from src.services.thinktogether.learning_content import safe_item


async def room_task(db, ctx, room_uuid):
    # Use the same room -> learner lock order as phase changes/exposure writes.
    room = await rooms._room(db, ctx, room_uuid, lock=True)
    await rooms.require_room_member(db, ctx.org_id, ctx.user.id, room.id)
    if room.phase not in ('individual_attempt', 'transfer'):
        raise HTTPException(409, 'Personal tasks are available during independent phases')
    cohort = await db.get(TTCohort, room.cohort_id)
    await rooms.require_paid(db, ctx.org_id, ctx.user.id, cohort.program_id)
    await learning.learner_lock(db, ctx.org_id, ctx.user.id)
    delivery = (await db.execute(select(TTDelivery).where(TTDelivery.org_id == ctx.org_id,
        TTDelivery.user_id == ctx.user.id, TTDelivery.room_id == room.id, TTDelivery.room_phase == room.phase))).scalar_one_or_none()
    if delivery is None:
        if room.phase == 'individual_attempt':
            item = await db.get(TTItem, room.case_item_id) if room.case_item_id else None
            if item is None or item.org_id != ctx.org_id or item.program_id != cohort.program_id or item.status != 'published' or item.is_sample or not item.approved_by or not item.approval_evidence or item.rubric.get('practice_mode', 'case') != 'case':
                raise HTTPException(409, 'An expert must assign an approved case before this session')
        else:
            candidates = await learning.unseen_items(db, ctx, cohort.program_id)
            shared = await db.get(TTItem, room.case_item_id) if room.case_item_id else None
            candidates = [i for i in candidates if shared and i.family_id != shared.family_id]
            personal = (await db.execute(select(TTAttempt).join(TTDelivery, TTAttempt.delivery_id == TTDelivery.id).where(
                TTAttempt.org_id == ctx.org_id, TTAttempt.user_id == ctx.user.id, TTDelivery.room_id == room.id,
                TTDelivery.room_phase == 'individual_attempt'))).scalars().first()
            evaluation = await learning.latest_evaluation(db, personal.id) if personal else None
            case = await db.get(TTItem, room.case_item_id) if room.case_item_id else None
            # Start with the discussed skill; a correct private explanation gets
            # a harder application, an error gets a simpler fresh context.
            preferred = [i for i in candidates if case and i.skill_id == case.skill_id]
            candidates = preferred
            if not candidates:
                support = await learning.shortage(db, ctx, cohort.program_id, 'No novel transfer case for this room', [case.skill_id] if case else [])
                await db.commit()
                raise HTTPException(409, {'message':'An expert must add a fresh transfer case', 'support_case_id':support.public_id})
            strong = evaluation is not None and evaluation.score is not None and evaluation.score >= .8
            item = sorted(candidates, key=lambda i:(-i.difficulty if strong else i.difficulty, i.id))[0]
        delivery = TTDelivery(org_id=ctx.org_id, user_id=ctx.user.id, item_id=item.id,
            room_id=room.id, room_phase=room.phase,
            phase='group_personal' if room.phase == 'individual_attempt' else 'independent',
            expires_at=utcnow()+timedelta(hours=4))
        db.add(delivery)
        await db.flush()
    else:
        item = await db.get(TTItem, delivery.item_id)
    previous = (await db.execute(select(TTAttempt).where(TTAttempt.delivery_id == delivery.id))).scalars().first()
    result = {'delivery_id':delivery.public_id, 'item':safe_item(item), 'phase':delivery.phase,
              'room_id':room.room_uuid, 'room_phase':room.phase, 'submitted_attempt_id':previous.public_id if previous else None}
    await db.commit()
    return result


async def validate_delivery_phase(db, ctx, delivery):
    if not delivery.room_id:
        return
    room = await db.get(TTRoom, delivery.room_id)
    if room is None or room.org_id != ctx.org_id:
        raise HTTPException(404, 'Room not found')
    room = await rooms._room(db, ctx, room.room_uuid, lock=True)
    if room.phase != delivery.room_phase:
        raise HTTPException(409, 'The room has advanced beyond this personal task')


async def solution_available(db, attempt):
    if not attempt.delivery_id:
        return True
    delivery = await db.get(TTDelivery, attempt.delivery_id)
    if not delivery or not delivery.room_id:
        return True
    room = await db.get(TTRoom, delivery.room_id)
    if room is None:
        return False
    return room.phase in ('discussion', 'summary') if delivery.room_phase == 'individual_attempt' else room.phase == 'summary'
