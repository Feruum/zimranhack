"""Psychometric answers never reach the generic educational AI grader."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlmodel import select


def context(user_id=101, permission=None):
    return SimpleNamespace(org_id=1, user=SimpleNamespace(id=user_id), is_staff=bool(permission),
                           can=lambda requested: requested == permission)


async def assessment(db, kind='reasoning'):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTProgram, TTSkill, TTItem, TTInstrument, TTConsent, TTDiagnosticRun
    program = TTProgram(org_id=1, title='Private criterion assessment fixture')
    db.add(program)
    await db.flush()
    skill = TTSkill(org_id=1, program_id=program.id, code='criterion', title='Criterion')
    db.add(skill)
    await db.flush()
    item = TTItem(org_id=1, program_id=program.id, skill_id=skill.id, family_id='private-cognitive',
                  title='Private assessment', prompt='Explain this assessment response.', kind=kind,
                  rubric={'dimensions': ['criterion']}, choices=[{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}],
                  answer_key={'choice': 'a'}, status='published', is_sample=False)
    domain = TTItem(org_id=1, program_id=program.id, skill_id=skill.id, family_id='domain-diagnostic',
                    title='Domain', prompt='Choose', choices=item.choices, answer_key={'choice': 'a'}, status='published', is_sample=False)
    db.add(item); db.add(domain)
    await db.flush()
    cognitive = TTInstrument(org_id=1, title='Licensed cognitive criterion', kind='cognitive', status='published',
                             is_sample=False, license_reference='expert-only scoring rights', approval_evidence='Specialist approved',
                             consent_text='Private specialist assessment', definition={'item_ids': [item.id], 'scoring': 'dimension_mean',
                             'dimensions': {'criterion': [item.id]}})
    domain_instrument = TTInstrument(org_id=1, title='Approved domain criterion', kind='domain', status='published',
                                    is_sample=False, license_reference='original', approval_evidence='Expert approval',
                                    consent_text='Consent', definition={'item_ids': [domain.id]})
    db.add(cognitive); db.add(domain_instrument)
    await db.flush()
    for instrument in [cognitive, domain_instrument]:
        db.add(TTConsent(org_id=1, user_id=101, instrument_id=instrument.id, version='1', accepted=True, text_snapshot=instrument.consent_text))
    db.add(TTDiagnosticRun(org_id=1, user_id=101, program_id=program.id, instrument_id=domain_instrument.id,
                          kind='domain', status='completed', item_ids=[domain.id], result={'dimension_scores': {'criterion': 1}}))
    await db.commit()
    await svc.save_profile(db, context(), {'program_id': program.public_id, 'goal': 'PMP',
                                          'exam_date': (svc.utcnow()+timedelta(days=365)).date().isoformat()})
    started = await svc.start_diagnostic(db, context(), 'cognitive', cognitive.public_id)
    return svc, program, skill, item, started


@pytest.mark.asyncio
async def test_cognitive_reasoning_requires_specialist_no_ai_outbox_and_specialist_completes_readiness(db, monkeypatch):
    from src.db.thinktogether.learning import TTAttempt, TTItem
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.learning_evidence import ReasoningGrade, latest_evaluation, learning_grade_job
    from src.services.thinktogether.jobs import enqueue_job, run_job
    monkeypatch.setenv('TT_REASONING_MODEL', 'gemini-3.5-flash')
    provider = AsyncMock(return_value=ReasoningGrade(score=.9, feedback='Must never be used for cognition', dimensions={'criterion': .9}))
    monkeypatch.setattr('src.services.ai.llm.client.generate', provider)
    svc, program, skill, item, started = await assessment(db)
    submitted = await svc.submit_attempt(db, context(), item.public_id, {'text': 'Private psychometric response'},
                                         'private-1', delivery_id=started['delivery_id'])
    assert submitted['status'] == 'needs_review' and submitted['score'] is None
    assert not (await db.exec(select(TTJob).where(TTJob.kind == 'learning.grade'))).all()
    attempt = (await db.exec(select(TTAttempt).where(TTAttempt.public_id == submitted['id']))).one()
    pending = await latest_evaluation(db, attempt.id)
    assert pending.score is None and 'psychometric specialist' in pending.feedback.lower()
    # Previously queued jobs must finish without a provider call or retry storm.
    job = await enqueue_job(db, 1, 'learning.grade', {'attempt_id': attempt.public_id}, 'legacy-cognitive-job')
    await db.commit()
    assert await run_job(db, job.id, {'learning.grade': learning_grade_job})
    assert job.status == 'done' and job.attempts == 1
    assert (await latest_evaluation(db, attempt.id)).score is None
    assert provider.await_count == 0
    assert not (await svc.learning_readiness(db, 1, 101))['cognitive_complete']
    await svc.expert_evaluate(db, context(202, 'psychometrics'), submitted['id'], .9, 'Licensed specialist reviewed the criterion response')
    assert (await svc.learning_readiness(db, 1, 101))['ready']
    assert (await svc.get_profile(db, context()))['cognitive_profile']['dimension_scores']['criterion'] == .9
    # Ordinary PMP reasoning retains its existing durable AI path.
    ordinary = TTItem(org_id=1, program_id=program.id, skill_id=skill.id, family_id='ordinary-pmp-reasoning',
                      title='PMP reasoning', prompt='Explain a project decision.', kind='reasoning', rubric={'dimensions': ['criterion']},
                      status='published', is_sample=False)
    db.add(ordinary)
    await db.commit()
    normal = await svc.submit_attempt(db, context(), ordinary.public_id, {'text': 'Ordinary learning response'}, 'ordinary-1')
    queued = (await db.exec(select(TTJob).where(TTJob.kind == 'learning.grade', TTJob.status == 'pending'))).all()
    assert len(queued) == 1 and queued[0].payload['attempt_id'] == normal['id']
    assert provider.await_count == 0


@pytest.mark.asyncio
async def test_closed_cognitive_item_uses_approved_deterministic_key_without_ai(db, monkeypatch):
    from src.db.thinktogether.learning import TTAttempt
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.learning_evidence import latest_evaluation
    monkeypatch.setenv('TT_REASONING_MODEL', 'gemini-3.5-flash')
    provider = AsyncMock()
    monkeypatch.setattr('src.services.ai.llm.client.generate', provider)
    svc, program, skill, item, started = await assessment(db, kind='single_choice')
    submitted = await svc.submit_attempt(db, context(), item.public_id, {'choice': 'a'}, 'private-closed', delivery_id=started['delivery_id'])
    attempt = (await db.exec(select(TTAttempt).where(TTAttempt.public_id == submitted['id']))).one()
    evaluation = await latest_evaluation(db, attempt.id)
    assert evaluation.source == 'deterministic' and evaluation.score == 1
    assert submitted['score'] is None and submitted['evaluation'] is None
    assert not (await db.exec(select(TTJob))).all() and provider.await_count == 0
