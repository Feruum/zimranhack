"""Publication must preserve a genuinely independent live practice bank."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlmodel import select


def ctx():
    return SimpleNamespace(org_id=1, user=SimpleNamespace(id=101), is_staff=True, can=lambda permission: True)


async def bank(db, count=3, status='draft'):
    from src.db.thinktogether.learning import TTProgram, TTSkill, TTItem
    program = TTProgram(org_id=1, title='Original expert-reviewed draft', status='draft', is_sample=False,
                        full_curriculum=True, blueprint_version='PMP-2026-07', approval_evidence='Expert curriculum approval')
    db.add(program)
    await db.flush()
    skill = TTSkill(org_id=1, program_id=program.id, code='principle', title='Principle')
    db.add(skill)
    await db.flush()
    items = []
    for n in range(count):
        item = TTItem(org_id=1, program_id=program.id, skill_id=skill.id, family_id=f'original-{n}',
                      title=f'Case {n}', prompt='Decide', choices=[{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}],
                      answer_key={'choice': 'a'}, status='published', is_sample=False,
                      approval_evidence='Expert approved case', approved_by=101)
        db.add(item)
        items.append(item)
    await db.flush()
    program.status = status
    db.add(program)
    await db.commit()
    return program, skill, items


def instrument(program, items, status='approved', **extra):
    from src.db.thinktogether.learning import TTInstrument
    return TTInstrument(org_id=program.org_id, title='Licensed domain diagnostic', kind='domain',
                        status=status, is_sample=False, license_reference='license/original', approval_evidence='Expert instrument approval',
                        consent_text='Consent to criterion assessment', definition={'item_ids': [i.id for i in items],
                        'scoring': 'dimension_mean', 'dimensions': {'principle': [i.id for i in items]}}, **extra)


@pytest.mark.asyncio
async def test_draft_instrument_does_not_remove_live_practice_cases(db):
    from src.services.thinktogether import learning as svc
    program, skill, items = await bank(db, status='published')
    draft = instrument(program, items, status='draft')
    db.add(draft)
    await db.commit()
    available = await svc.unseen_items(db, ctx(), program.id)
    assert {i.family_id for i in available} == {i.family_id for i in items}


@pytest.mark.asyncio
async def test_program_cannot_publish_when_diagnostics_take_its_entire_independent_bank(db):
    from src.services.thinktogether import learning as svc
    program, skill, items = await bank(db, status='approved')
    db.add(instrument(program, items, status='published'))
    await db.commit()
    with pytest.raises(HTTPException) as blocked:
        await svc.transition_content(db, ctx(), 'programs', program.public_id, 'published')
    assert blocked.value.status_code == 409
    assert 'independent practice bank' in str(blocked.value.detail).lower()
    assert program.status == 'approved'


@pytest.mark.asyncio
async def test_instrument_publication_cannot_steal_a_published_courses_minimum_bank(db):
    from src.services.thinktogether import learning as svc
    program, skill, items = await bank(db, status='published')
    proposed = instrument(program, [items[0]])
    db.add(proposed)
    await db.commit()
    with pytest.raises(HTTPException) as blocked:
        await svc.transition_content(db, ctx(), 'instruments', proposed.public_id, 'published')
    assert blocked.value.status_code == 409
    assert proposed.status == 'approved'
    assert len(await svc.unseen_items(db, ctx(), program.id)) == 3


@pytest.mark.asyncio
async def test_instrument_publication_allows_three_remaining_and_initial_draft_setup(db):
    from src.services.thinktogether import learning as svc
    program, skill, items = await bank(db, count=4, status='published')
    proposed = instrument(program, [items[0]])
    db.add(proposed)
    await db.commit()
    await svc.transition_content(db, ctx(), 'instruments', proposed.public_id, 'published')
    assert len(await svc.unseen_items(db, ctx(), program.id)) == 3
    # Initial instrument preparation may precede final curriculum authoring.
    draft_program, draft_skill, draft_items = await bank(db, count=1, status='draft')
    initial = instrument(draft_program, draft_items)
    db.add(initial)
    await db.commit()
    assert (await svc.transition_content(db, ctx(), 'instruments', initial.public_id, 'published'))['status'] == 'published'


@pytest.mark.asyncio
async def test_instrument_version_replacement_checks_post_publication_family_reservations(db):
    from src.services.thinktogether import learning as svc
    program, skill, items = await bank(db, count=4, status='published')
    old = instrument(program, [items[0]], status='published')
    db.add(old)
    await db.commit()
    replacement = instrument(program, [items[1]], series_id=old.series_id, version=2)
    db.add(replacement)
    await db.commit()
    await svc.transition_content(db, ctx(), 'instruments', replacement.public_id, 'published')
    assert old.status == 'retired'
    assert {i.family_id for i in await svc.unseen_items(db, ctx(), program.id)} == {items[0].family_id, items[2].family_id, items[3].family_id}

@pytest.mark.asyncio
async def test_shared_bank_helper_excludes_heldouts_lessons_and_published_instruments_only(db):
    from src.services.thinktogether import learning as svc
    from src.services.thinktogether.learning_content import validate_program_practice_bank
    from src.db.thinktogether.learning import TTLesson, TTMockForm
    program, skill, items = await bank(db, count=6)
    db.add(TTLesson(org_id=1, program_id=program.id, skill_id=skill.id, title='Worked example',
                    body_markdown='Expert-authored example', case_families=[items[0].family_id], status='published', is_sample=False))
    db.add(TTMockForm(org_id=1, program_id=program.id, title='Held-out form', form_code='A', item_ids=[items[1].id],
                      status='published', is_sample=False))
    db.add(instrument(program, [items[2]], status='published'))
    db.add(instrument(program, [items[3]], status='draft'))
    await db.commit()
    assert (await validate_program_practice_bank(db, program))[skill.public_id] == 3
    assert {i.id for i in await svc.unseen_items(db, ctx(), program.id)} == {i.id for i in items[3:]}


@pytest.mark.asyncio
async def test_instrument_family_alias_cannot_drain_another_live_program(db):
    from src.services.thinktogether import learning as svc
    live, live_skill, live_items = await bank(db, count=3, status='published')
    draft, draft_skill, aliases = await bank(db, count=1, status='draft')
    # The author reused a stable case family across program versions.
    assert aliases[0].family_id == live_items[0].family_id
    proposed = instrument(draft, aliases)
    db.add(proposed)
    await db.commit()
    with pytest.raises(HTTPException) as blocked:
        await svc.transition_content(db, ctx(), 'instruments', proposed.public_id, 'published')
    assert blocked.value.status_code == 409 and proposed.status == 'approved'
