"""AI authoring must survive restart and can never approve its own output."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.jobs import TTJob
from src.db.thinktogether.learning import TTProgram, TTSkill, TTItem, TTLesson, TTMockForm, TTInstrument
from src.services.thinktogether import content_generation as service
from src.services.thinktogether.jobs import run_job

REAL_ASK = service.ask


def ctx(staff=True, org_id=1):
    return SimpleNamespace(org_id=org_id, user=SimpleNamespace(id=101), is_staff=staff, can=lambda permission: staff)


def draft_item(label='Original context'):
    return dict(title=label, prompt=f'{label}: A team must choose a proportionate response to a changing project constraint.',
        kind='single_choice', choices=[{'id': 'a', 'text': 'Analyze the impact with the affected stakeholders.'},
                                       {'id': 'b', 'text': 'Ignore the new constraint and continue unchanged.'}],
        answer_key={'choice': 'a'}, rubric={}, explanation='Assessing the consequences supports an informed, proportionate response; ignoring the constraint leaves its effects unmanaged.')


async def fake_ai(job, schema, instruction):
    if schema is service.Outline:
        return service.Outline(description='An original structured preparation course requiring human technical review.',
            skills=[service.OutlineSkill(code=f'{prefix}.{n}', title=topic,
                description=f'Learn to apply {topic.lower()} in different project settings.')
                for prefix, topics in service.TOPICS.items() for n, topic in enumerate(topics, 1)])
    if schema is service.ModuleDraft:
        return service.ModuleDraft(title=instruction['skill']['title'], body_markdown=(
            'Explain the decision, examine constraints, compare alternatives and justify the next action. '
            'Consider stakeholder needs and the consequences for delivery. '*8),
            objectives=['Explain the principle.', 'Apply it to a fresh context.'],
            items=[service.DraftItem(**draft_item(f'Practice context {job.payload["module_index"]}-{n}')) for n in range(job.payload['request']['practice_items_per_skill'])])
    return service.MockBatch(items=[service.DraftItem(**draft_item(f'Mock context {job.payload["mock_index"]}-{n}')) for n in range(10)])


@pytest.fixture
def authoring(monkeypatch):
    monkeypatch.setattr(service, 'configured_model', lambda: 'configured-test-model')
    monkeypatch.setattr(service, 'ask', fake_ai)
    async def authorized(db, job):
        return ctx(org_id=job.org_id)
    monkeypatch.setattr(service, 'worker_context', authorized)


async def begin(db, key='one'):
    result = await service.start(db, ctx(), service.GenerationInput(idempotency_key=key, practice_items_per_skill=3, course_format='full_pmp'))
    return await service.get_run(db, ctx(), result['id'])


async def step(db, job):
    job.available_at = service.utcnow()-timedelta(seconds=1)
    db.add(job)
    await db.commit()
    result = await run_job(db, job.id, {service.KIND: service.generate_step})
    await db.refresh(job)
    return result


async def test_generation_admission_is_scoped_idempotent_and_bounded(db, org, authoring):
    first = await begin(db)
    assert (await begin(db)).id == first.id
    with pytest.raises(HTTPException) as reused:
        await service.start(db, ctx(), service.GenerationInput(idempotency_key='one', title='Different brief'))
    assert reused.value.status_code == 409
    await begin(db, 'two')
    with pytest.raises(HTTPException) as capped:
        await begin(db, 'three')
    assert capped.value.status_code == 409
    with pytest.raises(HTTPException) as denied:
        await service.start(db, ctx(False), service.GenerationInput(idempotency_key='denied'))
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as hidden:
        await service.get_run(db, ctx(org_id=2), first.job_uuid)
    assert hidden.value.status_code == 404
    assert await service.list_runs(db, ctx(org_id=2)) == []


async def test_unconfigured_ai_does_not_queue_or_fabricate_content(db, org, monkeypatch):
    monkeypatch.delenv('TT_CONTENT_MODEL', raising=False)
    monkeypatch.delenv('TT_REASONING_MODEL', raising=False)
    with pytest.raises(HTTPException) as missing:
        await begin(db)
    assert missing.value.status_code == 503
    assert not (await db.exec(select(TTJob))).all()
    assert not (await db.exec(select(TTProgram))).all()


async def test_complete_course_is_incremental_resumable_and_requires_human_review(db, org, authoring):
    job = await begin(db)
    assert await step(db, job)
    program = (await db.exec(select(TTProgram))).one()
    assert program.status == 'draft' and len((await db.exec(select(TTSkill))).all()) == 26
    from src.services.thinktogether.learning_content import transition_content, list_programs
    with pytest.raises(HTTPException) as early:
        await transition_content(db, ctx(), 'programs', program.public_id, 'review')
    assert early.value.status_code == 409
    assert await step(db, job)
    assert len((await db.exec(select(TTLesson))).all()) == 1
    assert len((await db.exec(select(TTItem))).all()) == 3
    # A fresh DB session resumes the persisted checkpoint, not an in-memory task.
    await db.commit()
    async with AsyncSession(db.bind, expire_on_commit=False) as restarted:
        resumed = await service.get_run(restarted, ctx(), job.job_uuid)
        for _ in range(62):
            assert await step(restarted, resumed)
        assert resumed.status == 'done' and resumed.payload['stage'] == 'ready_for_review'
        assert resumed.payload['completed_steps'] == 64 and resumed.attempts == 1
        assert len((await restarted.exec(select(TTLesson))).all()) == 26
        items = (await restarted.exec(select(TTItem))).all()
        assert len(items) == 438 and len({i.family_id for i in items}) == 438
        assert all(i.status == 'draft' and not i.approved_by for i in items)
        assert len((await restarted.exec(select(TTMockForm))).all()) == 2
        assert not (await restarted.exec(select(TTInstrument))).all()
        assert await list_programs(restarted, ctx(False)) == []
        with pytest.raises(HTTPException):
            await service.package_action(restarted, ctx(), resumed.job_uuid, 'publish', 'Publish without approval must fail.')
        approved = await service.package_action(restarted, ctx(), resumed.job_uuid, 'approve', 'Expert checked all original lessons, keys, cases and coverage.')
        assert approved['stage'] == 'approved'
        again = await service.package_action(restarted, ctx(), resumed.job_uuid, 'approve', 'Expert checked all original lessons, keys, cases and coverage.')
        assert again['revision'] == approved['revision']
        assert not [i for i in (await restarted.exec(select(TTItem))).all() if i.status == 'published']
        changed = await service.package_action(restarted, ctx(), resumed.job_uuid, 'request_changes', 'Expert requests a correction to the wording before publication.')
        assert changed['stage'] == 'ready_for_review'
        assert all(i.status == 'draft' for i in (await restarted.exec(select(TTItem))).all())
        await service.package_action(restarted, ctx(), resumed.job_uuid, 'approve', 'Expert rechecked the corrected draft and all answer keys.')
        published = await service.package_action(restarted, ctx(), resumed.job_uuid, 'publish', 'Expert explicitly authorizes publication of the approved package.')
        assert published['stage'] == 'published'
        assert (await service.package_action(restarted, ctx(), resumed.job_uuid, 'publish', 'Expert explicitly authorizes publication of the approved package.'))['revision'] == published['revision']
        assert len(await list_programs(restarted, ctx(False))) == 1
        assert not await run_job(restarted, resumed.id, {service.KIND: service.generate_step})


async def test_invalid_provider_step_rolls_back_and_retry_keeps_prior_drafts(db, org, authoring, monkeypatch):
    job = await begin(db)
    assert await step(db, job)
    async def invalid(*args):
        raise ValueError('private provider material must not enter the job error')
    monkeypatch.setattr(service, 'ask', invalid)
    assert not await step(db, job)
    assert job.payload['module_index'] == 0 and job.last_error == 'ValueError'
    assert not (await db.exec(select(TTLesson))).all()
    assert len((await db.exec(select(TTProgram))).all()) == 1
    monkeypatch.setattr(service, 'ask', fake_ai)
    assert await step(db, job)
    assert len((await db.exec(select(TTLesson))).all()) == 1
    assert job.payload['module_index'] == 1


async def test_cancellation_preserves_drafts_and_blocks_future_calls(db, org, authoring, monkeypatch):
    job = await begin(db)
    assert await step(db, job)
    cancelled = await service.control(db, ctx(), job.job_uuid, 'cancel')
    assert cancelled['stage'] == 'cancelled' and cancelled['status'] == 'done'
    assert (await db.exec(select(TTProgram))).one().curriculum['ai_generation']['state'] == 'cancelled'
    async def forbidden(*args):
        raise AssertionError('Cancelled generation must not call a provider')
    monkeypatch.setattr(service, 'ask', forbidden)
    assert not await run_job(db, job.id, {service.KIND: service.generate_step})


async def test_expert_draft_edits_validate_and_cannot_inject_approval(db, org, authoring):
    from src.routers.thinktogether.content_generation import edit
    job = await begin(db)
    await step(db, job)
    await step(db, job)
    lesson = (await db.exec(select(TTLesson))).one()
    with pytest.raises(HTTPException) as active:
        await edit('lessons', lesson.public_id, {'title': 'Human correction'}, ctx(), db)
    assert active.value.status_code == 409
    await service.control(db, ctx(), job.job_uuid, 'cancel')
    result = await edit('lessons', lesson.public_id, {'title': 'Human correction'}, ctx(), db)
    assert result['title'] == 'Human correction' and result['status'] == 'draft'
    with pytest.raises(HTTPException) as injection:
        await edit('lessons', lesson.public_id, {'status': 'published', 'approved_by': 101}, ctx(), db)
    assert injection.value.status_code == 422
    with pytest.raises(HTTPException) as denied:
        await edit('lessons', lesson.public_id, {'title': 'No permission'}, ctx(False), db)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as invalid:
        await edit('lessons', lesson.public_id, {'estimated_minutes': -1}, ctx(), db)
    assert invalid.value.status_code == 422


async def test_worker_rechecks_content_permission_before_any_provider_call(db, org, authoring, monkeypatch):
    job = await begin(db)
    async def revoked(db, record):
        raise HTTPException(403, 'Content access was revoked')
    async def forbidden(*args):
        raise AssertionError('Provider must not be called after permission revocation')
    monkeypatch.setattr(service, 'worker_context', revoked)
    monkeypatch.setattr(service, 'ask', forbidden)
    assert not await step(db, job)
    assert job.last_error == 'HTTPException'
    assert not (await db.exec(select(TTProgram))).all()


async def test_duplicate_generated_cases_do_not_become_false_new_families(db, org, authoring, monkeypatch):
    job = await begin(db)
    await step(db, job)
    await step(db, job)
    async def repeated(record, schema, instruction):
        draft = await fake_ai(record, schema, instruction)
        draft.items[0].prompt = draft_item('Practice context 0-0')['prompt']
        return draft
    monkeypatch.setattr(service, 'ask', repeated)
    assert not await step(db, job)
    assert job.payload['module_index'] == 1
    assert job.last_error == 'ValueError'
    assert len((await db.exec(select(TTLesson))).all()) == 1
    assert len((await db.exec(select(TTItem))).all()) == 3


async def test_generated_structure_cannot_smuggle_an_approval(db, org, authoring, monkeypatch):
    from src.services.ai.llm import client
    from config import config
    from src.services.thinktogether import ai_usage
    from src.services.thinktogether import authoring_provider
    job = await begin(db)
    valid = await fake_ai(job, service.Outline, {})
    async def injected(**kwargs):
        return {**valid.model_dump(), 'status': 'published', 'approved_by': 101}
    monkeypatch.setattr(client, 'generate', injected)
    monkeypatch.setattr(config, 'get_learnhouse_config', lambda: SimpleNamespace(ai_config=SimpleNamespace(is_ai_enabled=True)))
    monkeypatch.setattr(ai_usage, 'make_usage_callback', lambda **kwargs: None)
    monkeypatch.setattr(authoring_provider, 'build_authoring_model', lambda model: object())
    monkeypatch.setattr(service, 'ask', REAL_ASK)
    assert not await step(db, job)
    assert job.last_error == 'ValidationError'
    assert not (await db.exec(select(TTProgram))).all()
