"""Acceptance checks for persistent learning and evidence, without external AI."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlmodel import select


def context(user_id=101, staff=False):
    return SimpleNamespace(org_id=1, user=SimpleNamespace(id=user_id), is_staff=staff,
                           can=lambda permission: staff)


@pytest.mark.asyncio
async def test_learning_acceptance_persistence_and_answer_privacy(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTAttempt, TTItem
    staff, learner = context(staff=True), context()
    seeded = await svc.seed_sample(db, staff)
    assert seeded['program']['status'] == 'draft'
    assert not (await svc.launch_readiness(db, 1))['ready']
    assert await svc.list_programs(db, learner) == []
    item = (await db.exec(select(TTItem))).first()
    with pytest.raises(HTTPException) as forbidden:
        await svc.get_item(db, learner, item.public_id)
    assert forbidden.value.status_code == 404
    # Approved real content can be used after an explicit expert workflow.
    await svc.transition_content(db, staff, 'programs', seeded['program']['id'], 'review')
    await svc.transition_content(db, staff, 'programs', seeded['program']['id'], 'approved', evidence='Expert content review ticket')
    # Sample cannot be published under a full curriculum claim.
    with pytest.raises(HTTPException):
        await svc.transition_content(db, staff, 'programs', seeded['program']['id'], 'published')
    item.is_sample = False
    item.status = 'published'
    db.add(item)
    await db.commit()
    served = await svc.get_item(db, learner, item.public_id)
    assert 'answer_key' not in served and 'rubric' not in served
    result = await svc.submit_attempt(db, learner, item.public_id, {'choice': item.answer_key['choice']}, 'key-1')
    again = await svc.submit_attempt(db, learner, item.public_id, {'choice': item.answer_key['choice']}, 'key-1')
    assert result['id'] == again['id'] and result['score'] == 1
    assert len((await db.exec(select(TTAttempt))).all()) == 1
    assert (await svc.attempt_detail(db, learner, result['id']))['answer_key'] == item.answer_key
    with pytest.raises(HTTPException):
        await svc.attempt_detail(db, context(102), result['id'])
    with pytest.raises(HTTPException):
        await svc.submit_attempt(db, learner, item.public_id, {'choice': 'wrong'}, 'key-1')


@pytest.mark.asyncio
async def test_learning_evidence_requires_unseen_independent_distinct_days_and_replays(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTMastery, TTReview
    staff, learner = context(staff=True), context()
    await svc.seed_sample(db, staff)
    template = (await db.exec(select(TTItem))).first()
    now = svc.utcnow()
    attempts = []
    for n, days in enumerate([0, 1, 8]):
        item = TTItem(org_id=1, program_id=template.program_id, skill_id=template.skill_id,
                      family_id=f'independent-{n}', title='Fresh evidence', prompt='Decide',
                      choices=[{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}],
                      answer_key={'choice': 'a'}, status='published', is_sample=False)
        db.add(item)
        await db.commit()
        attempts.append(await svc.submit_attempt(db, learner, item.public_id, {'choice': 'a'}, f'e-{n}', now=now+timedelta(days=days)))
    mastery = (await db.exec(select(TTMastery))).first()
    assert mastery.state == 'retained'
    assert mastery.independent_days == 3
    assert len((await db.exec(select(TTReview))).all()) == 12
    # A hint disqualifies otherwise correct evidence; expert corrections replay.
    await svc.expert_evaluate(db, staff, attempts[-1]['id'], 0.0, 'Incorrect causal justification')
    mastery = (await db.exec(select(TTMastery))).first()
    assert mastery.state != 'retained'
    assert mastery.independent_days == 2


@pytest.mark.asyncio
async def test_learning_readiness_and_plan_fail_closed(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem
    staff, learner = context(staff=True), context()
    seeded = await svc.seed_sample(db, staff)
    initial = await svc.learning_readiness(db, 1, 101)
    assert initial == dict(profile_complete=False, domain_diagnostic_complete=False, cognitive_complete=False, ready=False)
    profile = await svc.save_profile(db, learner, {'goal': 'Pass PMP', 'exam_date': '2027-06-01', 'minutes_per_day': 20,
                                                  'timezone': 'Asia/Qyzylorda', 'program_id': seeded['program']['id']})
    assert profile['goal'] == 'Pass PMP'
    assert (await svc.learning_readiness(db, 1, 101))['profile_complete']
    plan = await svc.build_plan(db, learner, 20)
    assert plan['total_minutes'] <= 20
    assert plan['status'] == 'blocked'
    assert plan['support_case_id']
    with pytest.raises(HTTPException):
        await svc.start_diagnostic(db, learner, 'cognitive', None)


@pytest.mark.asyncio
async def test_reasoning_never_fabricates_grades_without_provider(db, monkeypatch):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem
    monkeypatch.delenv('TT_REASONING_MODEL', raising=False)
    await svc.seed_sample(db, context(staff=True))
    template = (await db.exec(select(TTItem))).first()
    item = TTItem(org_id=1, program_id=template.program_id, skill_id=template.skill_id,
                  family_id='reasoning-1', title='Explain', prompt='Explain a decision', kind='reasoning',
                  rubric={'dimensions': ['causal reasoning']}, status='published', is_sample=False)
    db.add(item)
    await db.commit()
    result = await svc.submit_attempt(db, context(), item.public_id, {'text': 'A thoughtful answer'}, 'reason-1')
    assert result['status'] == 'needs_review' and result['score'] is None

@pytest.mark.asyncio
async def test_package_validation_and_timed_mock_withholds_answers(db):
    from src.services.thinktogether import learning as svc
    from src.services.thinktogether import learning_curriculum as curriculum
    from src.db.thinktogether.learning import TTItem, TTMockForm
    staff = context(staff=True)
    await svc.seed_sample(db, staff)
    item = (await db.exec(select(TTItem))).first()
    form = TTMockForm(org_id=1, program_id=item.program_id, title='Preview mock', form_code='A',
                      item_ids=[item.id], duration_minutes=10, status='draft', is_sample=True)
    db.add(form)
    await db.commit()
    run = await curriculum.start_mock(db, staff, form.public_id)
    assert 'answer_key' not in run['questions'][0]['item']
    submitted = await curriculum.answer_mock(db, staff, run['id'], item.public_id,
                                             {'choice': item.answer_key['choice']}, 'mock-1')
    assert 'answer_key' not in submitted
    detail = await svc.attempt_detail(db, staff, submitted['id'])
    assert 'answer_key' not in detail
    finalized = await curriculum.finalize_mock(db, staff, run['id'])
    assert finalized['status'] == 'completed' and finalized['score'] == 1.0
    with pytest.raises(HTTPException):
        await curriculum.answer_mock(db, staff, run['id'], item.public_id, {'choice': 'b'}, 'mock-2')
    report = curriculum.validate_package({'program': {'blueprint_version': 'old'}, 'skills': [], 'items': [], 'lessons': [], 'mock_forms': []})
    assert not report['valid'] and 'blueprint' in str(report['errors']).lower()

@pytest.mark.asyncio
async def test_configured_reasoning_is_transactionally_queued_not_graded_inline(db, monkeypatch):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem
    from src.db.thinktogether.jobs import TTJob
    monkeypatch.setenv('TT_REASONING_MODEL', 'configured-provider-model')
    await svc.seed_sample(db, context(staff=True))
    template = (await db.exec(select(TTItem))).first()
    item = TTItem(org_id=1, program_id=template.program_id, skill_id=template.skill_id,
                  family_id='queued-reason', title='Explain', prompt='Explain a decision', kind='reasoning',
                  rubric={'dimensions': ['causal reasoning']}, status='published', is_sample=False)
    db.add(item)
    await db.commit()
    result = await svc.submit_attempt(db, context(), item.public_id, {'text': 'Reasoning evidence'}, 'queued-1')
    jobs = (await db.exec(select(TTJob).where(TTJob.kind == 'learning.grade'))).all()
    assert len(jobs) == 1 and jobs[0].payload['attempt_id'] == result['id']
    assert result['score'] is None


@pytest.mark.asyncio
async def test_diagnostics_consent_revision_and_private_profiles(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTInstrument, TTDiagnosticRun
    staff, learner = context(staff=True), context()
    seeded = await svc.seed_sample(db, staff)
    await svc.save_profile(db, learner, {'goal': 'PMP', 'exam_date': '2027-06-01', 'program_id': seeded['program']['id']})
    item = (await db.exec(select(TTItem))).first()
    item.is_sample, item.status = False, 'published'
    db.add(item)
    instrument = TTInstrument(org_id=1, title='Licensed expert instrument', kind='cognitive', status='published',
                              is_sample=False, license_reference='license-1', approval_evidence='expert approval',
                              consent_text='Consent to private criterion assessment',
                              definition={'item_ids': [item.id], 'scoring': 'dimension_mean', 'dimensions': {'reasoning': [item.id]}})
    db.add(instrument)
    await db.commit()
    with pytest.raises(HTTPException):
        await svc.start_diagnostic(db, learner, 'cognitive', instrument.public_id)
    await svc.give_consent(db, learner, instrument.public_id, True, '1')
    run = await svc.start_diagnostic(db, learner, 'cognitive', instrument.public_id)
    attempt = await svc.submit_attempt(db, learner, item.public_id, {'choice': item.answer_key['choice']},
                                       'diag-1', delivery_id=run['delivery_id'])
    assert (await svc.learning_readiness(db, 1, 101))['cognitive_complete']
    other_content_expert = context(102, staff=True)
    other_content_expert.can = lambda permission: permission == 'content'
    with pytest.raises(HTTPException):
        await svc.attempt_detail(db, other_content_expert, attempt['id'])
    await svc.expert_evaluate(db, staff, attempt['id'], .25, 'Expert corrected instrument score')
    assert (await svc.get_profile(db, learner))['cognitive_profile']['dimension_scores']['reasoning'] == .25
    await svc.give_consent(db, learner, instrument.public_id, False, '1')
    assert not (await svc.learning_readiness(db, 1, 101))['cognitive_complete']

@pytest.mark.asyncio
async def test_hint_family_reuse_and_preview_never_create_independent_evidence(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTMastery
    staff, learner = context(staff=True), context()
    await svc.seed_sample(db, staff)
    item = (await db.exec(select(TTItem))).first()
    # A staff preview of draft samples never contributes mastery.
    preview = await svc.submit_attempt(db, staff, item.public_id, {'choice': item.answer_key['choice']}, 'preview-1')
    mastery = (await db.exec(select(TTMastery))).first()
    assert mastery.independent_days == 0
    item.is_sample, item.status = False, 'published'
    db.add(item)
    await db.commit()
    served = await svc.issue_item(db, learner, item)
    await db.commit()
    await svc.hint(db, learner, served['delivery_id'])
    attempted = await svc.submit_attempt(db, learner, item.public_id, {'choice': item.answer_key['choice']},
                                         'hinted-1', delivery_id=served['delivery_id'])
    assert attempted['assisted'] and not attempted['independent_evidence']
    mastery = (await db.exec(select(TTMastery))).first()
    assert mastery.independent_days == 0


@pytest.mark.asyncio
async def test_http_content_grants_entitlement_and_safe_profile(db):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from src.routers.thinktogether import learning as routes
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem
    await svc.seed_sample(db, context(staff=True))
    item = (await db.exec(select(TTItem))).first()
    item.is_sample, item.status = False, 'published'
    db.add(item)
    await db.commit()
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/tt')
    app.dependency_overrides[routes.get_context] = lambda: context()
    async def session():
        yield db
    app.dependency_overrides[routes.get_db_session] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/v1/tt/content/sample')).status_code == 403
        assert (await client.get('/api/v1/tt/content')).status_code == 403
        assert (await client.get('/api/v1/tt/content/items/'+item.public_id)).status_code == 403
        assert (await client.get('/api/v1/tt/practice/next')).status_code == 403
        response = await client.get('/api/v1/tt/me/profile')
        assert response.status_code == 200 and response.json()['readiness']['ready'] is False


@pytest.mark.asyncio
async def test_pending_reasoning_review_stays_due_until_expert_scores(db, monkeypatch):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTReview
    monkeypatch.delenv('TT_REASONING_MODEL', raising=False)
    staff, learner = context(staff=True), context()
    await svc.seed_sample(db, staff)
    template = (await db.exec(select(TTItem))).first()
    template.is_sample, template.status = False, 'published'
    db.add(template)
    await db.commit()
    source = await svc.submit_attempt(db, learner, template.public_id, {'choice': template.answer_key['choice']}, 'source-review')
    review = (await db.exec(select(TTReview))).first()
    reasoning = TTItem(org_id=1, program_id=template.program_id, skill_id=template.skill_id,
                       family_id='review-fresh', title='Review reasoning', prompt='Explain your trade-off', kind='reasoning',
                       rubric={'dimensions': ['reason']}, is_sample=False, status='published')
    db.add(reasoning)
    await db.commit()
    issued = await svc.issue_item(db, learner, reasoning, phase='review', review_id=review.id)
    await db.commit()
    attempt = await svc.submit_attempt(db, learner, reasoning.public_id, {'text': 'My reasoning'}, 'pending-review', delivery_id=issued['delivery_id'])
    assert review.status == 'pending'
    await svc.expert_evaluate(db, staff, attempt['id'], .9, 'Reasoning satisfies the rubric')
    assert review.status == 'completed'

@pytest.mark.asyncio
async def test_curriculum_roundtrip_stays_draft_and_lesson_exposure_is_persistent(db):
    from src.services.thinktogether import learning as svc, learning_curriculum as curriculum
    from src.db.thinktogether.learning import TTExposure
    staff = context(staff=True)
    seeded = await svc.seed_sample(db, staff)
    package = await curriculum.export_package(db, staff, seeded['program']['id'])
    assert package['validation']['valid']
    imported = await curriculum.import_package(db, staff, package)
    assert imported['program']['status'] == 'draft' and imported['program']['is_sample']
    summaries = await curriculum.lessons(db, staff, imported['program']['id'])
    assert len(summaries) == 3 and 'body_markdown' not in summaries[0]
    lesson = await curriculum.lessons(db, staff, imported['program']['id'], summaries[0]['id'])
    assert 'Decision process' in lesson['body_markdown']
    assert len((await db.exec(select(TTExposure))).all()) == 1
    completed = await curriculum.complete_lesson(db, staff, lesson['id'])
    assert completed['mastery_evidence'] is False


@pytest.mark.asyncio
async def test_adaptive_plan_prioritizes_due_review_and_versions_within_budget(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem
    staff, learner = context(staff=True), context()
    seeded = await svc.seed_sample(db, staff)
    await svc.save_profile(db, learner, {'goal': 'PMP', 'exam_date': '2027-06-01', 'program_id': seeded['program']['id'], 'minutes_per_day': 5})
    source = (await db.exec(select(TTItem))).first()
    source.is_sample, source.status = False, 'published'
    db.add(source)
    await db.commit()
    await svc.submit_attempt(db, learner, source.public_id, {'choice': source.answer_key['choice']}, 'past-evidence', now=svc.utcnow()-timedelta(days=2))
    fresh = TTItem(org_id=1, program_id=source.program_id, skill_id=source.skill_id, family_id='review-transfer',
                   title='Fresh transfer', prompt='Select', choices=[{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}],
                   answer_key={'choice': 'a'}, status='published', is_sample=False, estimated_minutes=3)
    db.add(fresh)
    await db.commit()
    plan = await svc.build_plan(db, learner, 5)
    assert plan['activities'][0]['kind'] == 'review'
    assert plan['total_minutes'] == 3
    assert (await svc.build_plan(db, learner, 5))['version'] == plan['version']+1


@pytest.mark.asyncio
async def test_mock_deadline_closes_run_and_immutable_attempts_reject_updates(db):
    from src.services.thinktogether import learning as svc, learning_curriculum as curriculum
    from src.db.thinktogether.learning import TTItem, TTMockForm, TTMockRun, TTAttempt
    staff = context(staff=True)
    await svc.seed_sample(db, staff)
    item = (await db.exec(select(TTItem))).first()
    form = TTMockForm(org_id=1, program_id=item.program_id, title='Timed sample', form_code='timed', item_ids=[item.id])
    db.add(form)
    await db.commit()
    issued = await curriculum.start_mock(db, staff, form.public_id)
    run = (await db.exec(select(TTMockRun))).first()
    run.deadline = svc.utcnow()-timedelta(seconds=1)
    db.add(run)
    await db.commit()
    with pytest.raises(HTTPException) as denied:
        await curriculum.answer_mock(db, staff, issued['id'], item.public_id, {'choice': 'a'}, 'late')
    assert denied.value.status_code == 409
    closed = await curriculum.mock_detail(db, staff, issued['id'])
    assert closed['status'] == 'completed' and closed['score'] == 0
    submitted = await svc.submit_attempt(db, staff, item.public_id, {'choice': 'a'}, 'immutable')
    attempt = (await db.exec(select(TTAttempt))).first()
    attempt.response = {'choice': 'b'}
    db.add(attempt)
    with pytest.raises(ValueError, match='immutable'):
        await db.flush()
    await db.rollback()

@pytest.mark.asyncio
async def test_matching_items_validate_and_score_without_exposing_mapping(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTSkill, TTItem
    staff, learner = context(staff=True), context()
    seeded = await svc.seed_sample(db, staff)
    skill = (await db.exec(select(TTSkill))).first()
    content = {'program_id': seeded['program']['id'], 'skill_id': skill.public_id,
               'family_id': 'match-original', 'title': 'Match response to uncertainty', 'prompt': 'Match each situation to its response.',
               'kind': 'matching', 'is_sample': False,
               'choices': [{'id': 'l1', 'text': 'Uncertain event', 'side': 'left'}, {'id': 'l2', 'text': 'Observed impediment', 'side': 'left'},
                           {'id': 'r1', 'text': 'Risk response', 'side': 'right'}, {'id': 'r2', 'text': 'Issue resolution', 'side': 'right'}],
               'answer_key': {'matches': {'l1': 'r1', 'l2': 'r2'}}}
    created = await svc.create_item(db, staff, content)
    item = (await db.exec(select(TTItem).where(TTItem.public_id == created['id']))).first()
    item.status = 'published'
    db.add(item)
    await db.commit()
    served = await svc.get_item(db, learner, item.public_id)
    assert 'answer_key' not in served and served['choices'][0]['side'] == 'left'
    result = await svc.submit_attempt(db, learner, item.public_id, {'matches': {'l1': 'r1', 'l2': 'r2'}}, 'match-1')
    assert result['score'] == 1
    with pytest.raises(HTTPException) as invalid:
        await svc.submit_attempt(db, learner, item.public_id, {'matches': {'l1': 'unknown', 'l2': 'r2'}}, 'match-invalid')
    assert invalid.value.status_code == 422
