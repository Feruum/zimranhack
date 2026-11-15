from types import SimpleNamespace
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.tests.thinktogether.test_rooms import room_setup


@pytest.mark.asyncio
async def test_private_room_deliveries_resume_and_transfer_is_new(db, room_setup, monkeypatch):
    from src.db.thinktogether.learning import TTProgram, TTSkill, TTItem, TTDelivery
    from src.services.thinktogether import rooms, learning
    from src.services.thinktogether.room_learning import room_task
    async def access(*args, **kwargs): pass
    monkeypatch.setattr(rooms, 'require_paid', access)
    cohort, _, room, ctx = room_setup
    program = TTProgram(org_id=ctx.org_id, title='Program')
    db.add(program)
    await db.flush()
    skill = TTSkill(org_id=ctx.org_id, program_id=program.id, code='skill', title='Decision', domain='People')
    db.add(skill)
    await db.flush()
    items = [TTItem(org_id=ctx.org_id, program_id=program.id, skill_id=skill.id, family_id=f'family-{n}',
                    title=f'Case {n}', prompt='Decide', status='published', is_sample=False,
                    choices=[{'id':'a','text':'A'},{'id':'b','text':'B'}], answer_key={'choice':'a'},
                    hints=['Help'], difficulty=n+1, approved_by=ctx.user.id, approval_evidence='Expert ticket') for n in range(3)]
    db.add_all(items)
    await db.flush()
    cohort.program_id, room.case_item_id, room.phase = program.id, items[0].id, 'individual_attempt'
    db.add_all([cohort, room])
    await db.commit()
    task = await room_task(db, ctx, room.room_uuid)
    assert task['delivery_id'] == (await room_task(db, ctx, room.room_uuid))['delivery_id']
    assert 'answer_key' not in task['item']
    with pytest.raises(HTTPException):
        await learning.hint(db, ctx, task['delivery_id'])
    first = await learning.submit_attempt(db, ctx, items[0].public_id, {'choice':'b'}, 'room-answer', delivery_id=task['delivery_id'])
    assert first['independent_evidence'] is False and 'answer_key' not in first
    assert first['score'] is None and first['evaluation'] is None
    private_detail = await learning.attempt_detail(db, ctx, first['id'])
    assert 'answer_key' not in private_detail
    assert private_detail['score'] is None and private_detail['evaluation'] is None
    room.phase = 'discussion'
    db.add(room)
    await rooms.expose_case_to_members(db, room)
    await db.commit()
    discussion_detail = await learning.attempt_detail(db, ctx, first['id'])
    assert discussion_detail['score'] == 0 and discussion_detail['evaluation']
    assert discussion_detail['answer_key'] == items[0].answer_key
    room.phase = 'transfer'
    db.add(room)
    await db.commit()
    transfer = await room_task(db, ctx, room.room_uuid)
    assert transfer['item']['id'] != items[0].public_id
    assert len((await db.execute(select(TTDelivery))).scalars().all()) == 2
    room.phase = 'summary'
    db.add(room)
    await db.commit()
    with pytest.raises(HTTPException) as stale:
        await learning.submit_attempt(db, ctx, transfer['item']['id'], {'choice':'a'}, 'late-transfer', delivery_id=transfer['delivery_id'])
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_two_independent_days_demonstrate_transfer(db):
    from src.services.thinktogether import learning
    from src.db.thinktogether.learning import TTItem, TTMastery
    staff = SimpleNamespace(org_id=1, user=SimpleNamespace(id=101), can=lambda p:True)
    await learning.seed_sample(db, staff)
    template = (await db.execute(select(TTItem))).scalars().first()
    now = learning.utcnow()
    for n in range(2):
        item = TTItem(org_id=1, program_id=template.program_id, skill_id=template.skill_id,
                      family_id=f'transfer-{n}', title='Fresh', prompt='Decide', status='published', is_sample=False,
                      choices=[{'id':'a','text':'A'}], answer_key={'choice':'a'})
        db.add(item)
        await db.commit()
        await learning.submit_attempt(db, staff, item.public_id, {'choice':'a'}, f'independent-{n}', now=now+timedelta(days=n))
    mastery = (await db.execute(select(TTMastery))).scalars().first()
    assert mastery.state == 'independent'
