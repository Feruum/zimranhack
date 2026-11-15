"""Expert policy, real planning persistence and offline grading calibration gates."""
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlmodel import select


def context(user_id=101, staff=False):
    return SimpleNamespace(org_id=1, user=SimpleNamespace(id=user_id), is_staff=staff, can=lambda permission: staff)


def policy(version='v1', chunk=3, new=5, worked=True):
    return {'version': version, 'rights_reference': 'Expert confirms licensed use for initial instructional support',
            'default': {'chunk_minutes': 15, 'new_material_minutes': 30, 'worked_example': False},
            'rules': [{'id': 'criterion-support', 'dimension': 'reasoning', 'min_score': 0, 'max_score': .6,
                       'recommendations': {'chunk_minutes': chunk, 'new_material_minutes': new, 'worked_example': worked},
                       'rationale': 'Expert-authored criterion rule recommends this initial study format.'}]}


def test_policy_has_no_fabricated_defaults_or_unbounded_recommendations():
    from src.services.thinktogether.learning_support import validate_support_policy, recommend_support
    with pytest.raises(ValueError, match='policy'):
        validate_support_policy({'dimensions': {'reasoning': [1]}}, required=True)
    definition = {'dimensions': {'reasoning': [1]}, 'support_policy': policy()}
    recommendation = recommend_support(definition, {'reasoning': .4})
    assert recommendation['recommendations']['chunk_minutes'] == 3
    assert recommendation['rule_id'] == 'criterion-support'
    definition['support_policy']['rules'][0]['recommendations']['chunk_minutes'] = 999
    with pytest.raises(ValueError):
        validate_support_policy(definition, required=True)
    definition['support_policy'] = policy()
    definition['support_policy']['rules'][0]['dimension'] = 'unmeasured'
    with pytest.raises(ValueError):
        validate_support_policy(definition, required=True)


@pytest.mark.asyncio
async def test_two_expert_policies_change_initial_plan_and_independent_evidence_overrides(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTLesson, TTInstrument, TTDiagnosticRun, TTConsent
    seeded = await svc.seed_sample(db, context(staff=True))
    template = (await db.exec(select(TTItem))).first()
    template.status, template.is_sample = 'published', False
    db.add(template)
    lesson = (await db.exec(select(TTLesson).where(TTLesson.skill_id == template.skill_id))).first()
    lesson.status, lesson.is_sample, lesson.estimated_minutes, lesson.case_families = 'published', False, 12, []
    db.add(lesson)
    for n in range(3):
        db.add(TTItem(org_id=1, program_id=template.program_id, skill_id=template.skill_id, family_id=f'support-fresh-{n}',
                      title='Independent transfer', prompt='Decide', choices=template.choices, answer_key=template.answer_key,
                      estimated_minutes=5, is_sample=False, status='published'))
    await db.commit()
    for user_id, selected_policy in [(101, policy('short', 3, 5, True)), (102, policy('long', 12, 20, False))]:
        await svc.save_profile(db, context(user_id), {'goal': 'PMP', 'exam_date': (svc.utcnow()+timedelta(days=365)).date().isoformat(),
                              'program_id': seeded['program']['id'], 'minutes_per_day': 20})
        instrument = TTInstrument(org_id=1, title='Licensed criterion instrument', kind='cognitive', status='published', is_sample=False,
                                  license_reference='license/adaptive-rights', approval_evidence='Expert approved rules',
                                  consent_text='Initial support use', definition={'dimensions': {'reasoning': [template.id]},
                                  'support_policy': selected_policy})
        db.add(instrument)
        await db.flush()
        db.add(TTConsent(org_id=1, user_id=user_id, instrument_id=instrument.id, version='1', accepted=True, text_snapshot='Initial support use'))
        db.add(TTDiagnosticRun(org_id=1, user_id=user_id, program_id=template.program_id, instrument_id=instrument.id,
                              kind='cognitive', status='completed', completed_at=svc.utcnow(), item_ids=[template.id],
                              result={'dimension_scores': {'reasoning': .4}}))
    await db.commit()
    short, long = await svc.build_plan(db, context(101), 20), await svc.build_plan(db, context(102), 20)
    assert short['total_minutes'] <= 5 and long['total_minutes'] > short['total_minutes']
    assert short['activities'][0]['support']['policy_version'] == 'short'
    assert short['activities'][0]['support']['recommendations']['worked_example'] is True
    assert all('dimension_scores' not in a['support'] for a in short['activities'])
    assert any('initial' in explanation.lower() for explanation in short['rationale'])
    await svc.submit_attempt(db, context(101), template.public_id, {'choice': template.answer_key['choice']}, 'performance-override')
    overridden = await svc.build_plan(db, context(101), 20)
    practice = next(a for a in overridden['activities'] if a['kind'] == 'practice')
    assert practice['support']['source'] == 'independent_performance'
    assert practice['minutes'] == 5


@pytest.mark.asyncio
async def test_cognitive_publication_requires_licensed_versioned_support_policy(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTInstrument
    await svc.seed_sample(db, context(staff=True))
    item = (await db.exec(select(TTItem))).first()
    item.status, item.is_sample = 'published', False
    db.add(item)
    instrument = TTInstrument(org_id=1, title='Missing support policy', kind='cognitive', status='approved', is_sample=False,
                              license_reference='licensed', approval_evidence='expert review', consent_text='Consent',
                              definition={'item_ids': [item.id], 'dimensions': {'reasoning': [item.id]}, 'scoring': 'dimension_mean'})
    db.add(instrument)
    await db.commit()
    with pytest.raises(HTTPException) as blocked:
        await svc.transition_content(db, context(staff=True), 'instruments', instrument.public_id, 'published')
    assert blocked.value.status_code == 409
    instrument.definition = dict(instrument.definition, support_policy=policy())
    db.add(instrument)
    await db.commit()
    published = await svc.transition_content(db, context(staff=True), 'instruments', instrument.public_id, 'published')
    assert published['status'] == 'published'


def calibration_module():
    script = Path(__file__).resolve().parents[3] / 'scripts' / 'evaluate_thinktogether_grading.py'
    spec = importlib.util.spec_from_file_location('tt_grading_calibration', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def labeled_example(index):
    return {'id': f'expert-{index}', 'prompt': f'Original expert scenario {index}',
            'rubric': {'version': 'rubric-1', 'dimensions': ['reasoning']}, 'response': {'text': 'Expert-labeled candidate reasoning.'},
            'expert': {'score': .8, 'dimensions': {'reasoning': .8}, 'reviewer_id': 'expert-licensed', 'rationale': 'Reviewed against rubric'}}


def test_calibration_rejects_small_or_invalid_sets_and_reports_no_release_flag(tmp_path, capsys):
    cli = calibration_module()
    source = tmp_path / 'labels.jsonl'
    source.write_text('\n'.join(json.dumps(labeled_example(n)) for n in range(99)), encoding='utf-8')
    with pytest.raises(ValueError, match='100'):
        cli.load_examples(source)
    source.write_text('\n'.join(json.dumps(labeled_example(n)) for n in range(100)), encoding='utf-8')
    samples = cli.load_examples(source)
    assert len(samples) == 100
    assert cli.main(['--input', str(source), '--check-input']) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked['provider_evaluation'] == 'not_run' and checked['examples'] == 100
    reports = [{'id': e['id'], 'expert_score': .8, 'model_score': .8, 'source': 'ai',
                'expert_dimensions': {'reasoning': .8}, 'model_dimensions': {'reasoning': .8}} for e in samples]
    result = cli.summarize(reports, model='configured-model', dataset_sha256='real-input-hash', threshold=.8, tolerance=.1)
    assert result['agreement']['exact_score'] == 1
    assert result['confusion']['true_positive'] == 100
    assert 'release_approved' not in result and result['disagreements'] == []
    source.write_text('\n'.join(json.dumps(labeled_example(0)) for _ in range(100)), encoding='utf-8')
    with pytest.raises(ValueError, match='unique'):
        cli.load_examples(source)
