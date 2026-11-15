"""Real persistence acceptance for authored learning mechanics and transfer."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlmodel import select


def context(staff=True):
    return SimpleNamespace(org_id=1, user=SimpleNamespace(id=101), is_staff=staff, can=lambda permission: staff)


async def setup(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTSkill
    seed = await svc.seed_sample(db, context())
    await svc.save_profile(db, context(), {'program_id': seed['program']['id'], 'goal': 'PMP',
                                         'exam_date': (svc.utcnow()+timedelta(days=365)).date().isoformat()})
    items = (await db.exec(select(TTItem).order_by(TTItem.id))).all()
    for item in items:
        item.is_sample, item.status = False, 'published'
        db.add(item)
    await db.commit()
    return svc, items


@pytest.mark.asyncio
async def test_quest_error_selects_fresh_case_on_same_principle_before_other_skills(db):
    from src.db.thinktogether.learning import TTItem
    svc, items = await setup(db)
    failed = items[1]
    fresh = TTItem(org_id=1, program_id=failed.program_id, skill_id=failed.skill_id, family_id='fresh-same-principle',
                   title='Risk transfer', prompt='Use the same principle in a new situation', choices=failed.choices,
                   answer_key=failed.answer_key, status='published', is_sample=False)
    db.add(fresh)
    await db.commit()
    await svc.submit_attempt(db, context(), failed.public_id, {'choice': 'b'}, 'quest-error')
    next_case = await svc.practice_next(db, context())
    assert next_case['item']['id'] == fresh.public_id
    assert next_case['phase'] == 'independent'
    assert next_case['selection_reason'] == 'same_principle_after_error'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,field', [('retell', 'source_material'), ('think_first', 'ai_answer')])
async def test_authored_mechanics_coached_then_novel_transfer_never_mastery(db, monkeypatch, mode, field):
    from src.db.thinktogether.learning import TTItem, TTMastery
    monkeypatch.delenv('TT_REASONING_MODEL', raising=False)
    svc, items = await setup(db)
    source = items[1]
    coached = TTItem(org_id=1, program_id=source.program_id, skill_id=source.skill_id, family_id=f'{mode}-study',
                     title=f'Authored {mode}', prompt='Explain the principle and justify it.', kind='reasoning',
                     rubric={'practice_mode': mode, field: 'Expert-approved authored teaching material.',
                             'dimensions': ['reasoning'], 'grading_secret': 'must stay private'},
                     status='published', is_sample=False)
    db.add(coached)
    await db.commit()
    served = await svc.practice_next(db, context(), mode=mode)
    assert served['phase'] == 'coached' and served['item']['practice_mode'] == mode
    assert served['item'][field] and 'rubric' not in served['item'] and 'grading_secret' not in str(served['item'])
    submitted = await svc.submit_attempt(db, context(), coached.public_id, {'text': 'I can explain this decision.'},
                                         f'{mode}-submitted', delivery_id=served['delivery_id'])
    assert submitted['assisted'] and not submitted['independent_evidence']
    await svc.expert_evaluate(db, context(), submitted['id'], 1, 'Expert rubric review')
    mastery = (await db.exec(select(TTMastery).where(TTMastery.skill_id == source.skill_id))).first()
    assert mastery.independent_days == 0
    transfer = await svc.practice_next(db, context(), mode='case')
    assert transfer['item']['id'] == source.public_id and transfer['phase'] == 'independent'
    assert transfer['selection_reason'] == 'same_principle_after_coached_practice'


@pytest.mark.asyncio
async def test_empty_authored_mechanics_bank_reports_honest_shortage(db):
    svc, items = await setup(db)
    with pytest.raises(HTTPException) as missing:
        await svc.practice_next(db, context(), mode='retell')
    assert missing.value.status_code == 409 and missing.value.detail['support_case_id']


def test_coached_metadata_requires_reasoning_authored_source_and_safe_whitelist():
    from src.services.thinktogether.learning_content import validate_item, safe_item
    from src.db.thinktogether.learning import TTItem
    base = {'title': 'Retell', 'prompt': 'Explain', 'family_id': 'retell', 'kind': 'reasoning',
            'rubric': {'practice_mode': 'retell', 'dimensions': ['reasoning']}}
    with pytest.raises(HTTPException):
        validate_item(base)
    base['rubric']['source_material'] = 'Expert-authored explanation'
    validate_item(base)
    base['kind'] = 'single_choice'
    base['choices'] = [{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}]
    base['answer_key'] = {'choice': 'a'}
    with pytest.raises(HTTPException):
        validate_item(base)


def test_coached_families_cannot_satisfy_a_full_independent_practice_bank():
    from src.services.thinktogether.learning_curriculum import validate_package, BLUEPRINT, WEIGHTS, TASKS
    package = {'program': {'title': 'Full curriculum draft', 'full_curriculum': True, 'blueprint_version': BLUEPRINT,
                           'curriculum': {'domain_weights': WEIGHTS, 'task_coverage': sorted(TASKS)}},
               'skills': [{'code': 'principle', 'title': 'Principle', 'domain': 'People'}],
               'lessons': [{'title': 'Lesson', 'skill_code': 'principle', 'body_markdown': 'Authored lesson'}],
               'items': [{'code': f'coach-{n}', 'skill_code': 'principle', 'family_id': f'coached-family-{n}',
                          'title': 'Retell', 'prompt': 'Explain', 'kind': 'reasoning',
                          'rubric': {'dimensions': ['reasoning'], 'practice_mode': 'retell', 'source_material': 'Authored source'}} for n in range(3)],
               'mock_forms': []}
    report = validate_package(package)
    assert not report['valid']
    assert any('three practice families' in message for message in report['errors'])
