"""Expert-authored initial support, superseded by independent learning evidence.

Policy v1 uses ordered, inclusive criterion intervals; the first matching rule
wins. All defaults and thresholds come from the licensed instrument's author.
The bounds below are scheduling limits, not cognitive norms or classifications.
No cognitive scores or reports are sent to a language model by this module.
"""
import hashlib
import json
import math


def _recommendations(value):
    if not isinstance(value, dict) or set(value) != {'chunk_minutes', 'new_material_minutes', 'worked_example'}:
        raise ValueError('Support policy recommendations require the three supported scheduling fields')
    if type(value['chunk_minutes']) is not int or not 1 <= value['chunk_minutes'] <= 60:
        raise ValueError('Support policy chunk_minutes must be an integer from 1 to 60')
    if type(value['new_material_minutes']) is not int or not 0 <= value['new_material_minutes'] <= 240:
        raise ValueError('Support policy new_material_minutes must be an integer from 0 to 240')
    if type(value['worked_example']) is not bool:
        raise ValueError('Support policy worked_example must be boolean')


def validate_support_policy(definition, *, required=False):
    policy = definition.get('support_policy') if isinstance(definition, dict) else None
    if policy is None and not required:
        return None
    if not isinstance(policy, dict):
        raise ValueError('An expert-authored versioned support policy is required')
    if set(policy) != {'version', 'rights_reference', 'default', 'rules'}:
        raise ValueError('Support policy requires version, adaptive-use rights, explicit defaults and ordered rules')
    if any(not isinstance(policy[k], str) or not policy[k].strip() or len(policy[k]) > 2000 for k in ('version', 'rights_reference')):
        raise ValueError('Support policy version and licensed adaptive-use rights are required')
    _recommendations(policy['default'])
    if not isinstance(policy['rules'], list) or not 1 <= len(policy['rules']) <= 100:
        raise ValueError('Support policy needs 1–100 ordered expert-authored rules')
    dimensions = set(definition.get('dimensions', {'criterion': []}))
    seen = set()
    for rule in policy['rules']:
        if not isinstance(rule, dict) or set(rule) != {'id', 'dimension', 'min_score', 'max_score', 'recommendations', 'rationale'}:
            raise ValueError('Support policy rule fields are invalid')
        if not isinstance(rule['id'], str) or not rule['id'].strip() or rule['id'] in seen:
            raise ValueError('Support policy rule IDs must be unique')
        seen.add(rule['id'])
        if not isinstance(rule['dimension'], str) or rule['dimension'] not in dimensions:
            raise ValueError('Support policy may only reference measured instrument dimensions')
        low, high = rule['min_score'], rule['max_score']
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in (low, high)) or not 0 <= low <= high <= 1:
            raise ValueError('Support policy criterion bounds must be finite and between zero and one')
        if not isinstance(rule['rationale'], str) or not rule['rationale'].strip() or len(rule['rationale']) > 2000:
            raise ValueError('Support policy rules need an expert rationale')
        _recommendations(rule['recommendations'])
    return policy


def recommend_support(definition, scores):
    policy = validate_support_policy(definition, required=True)
    if not isinstance(scores, dict) or not scores or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
        raise ValueError('Completed criterion scores are required for initial support')
    if any(rule['dimension'] not in scores for rule in policy['rules']):
        raise ValueError('Support policy dimension scores are missing')
    selected = next((r for r in policy['rules'] if r['min_score'] <= scores[r['dimension']] <= r['max_score']), None)
    return {'source': 'initial_assessment', 'policy_version': policy['version'],
            'policy_sha256': hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'rule_id': selected['id'] if selected else 'default',
            'recommendations': dict(selected['recommendations'] if selected else policy['default']),
            'explanation': selected['rationale'] if selected else 'The expert-authored initial support defaults apply.'}


def cognitive_support_snapshot(instrument, scores):
    try:
        result = recommend_support(instrument.definition, scores)
    except (ValueError, TypeError):
        return {'source': 'unavailable', 'explanation': 'No approved usable initial support policy is available; no cognitive assumptions were made.'}
    return dict(result, instrument_id=instrument.public_id, instrument_version=instrument.version)


async def load_plan_support(db, ctx, profile):
    """Resolve trusted completed evidence, never a client-editable profile blob."""
    from sqlmodel import select
    from src.db.thinktogether.learning import TTDiagnosticRun, TTInstrument, TTAttempt, TTItem
    from src.services.thinktogether.learning import consent_current
    from src.services.thinktogether.learning_evidence import latest_evaluation
    initial = {'source': 'unavailable', 'explanation': 'Initial assessment support is unavailable; the standard study plan applies.'}
    if not profile:
        return initial, {}
    runs = (await db.execute(select(TTDiagnosticRun).where(TTDiagnosticRun.org_id == ctx.org_id,
        TTDiagnosticRun.user_id == ctx.user.id, TTDiagnosticRun.kind == 'cognitive',
        TTDiagnosticRun.status == 'completed').order_by(TTDiagnosticRun.id.desc()))).scalars().all()
    for run in runs:
        instrument = await db.get(TTInstrument, run.instrument_id)
        if instrument and instrument.org_id == ctx.org_id and instrument.kind == 'cognitive' and instrument.status == 'published' and not instrument.is_sample and instrument.approval_evidence and instrument.license_reference and await consent_current(db, ctx.org_id, ctx.user.id, instrument):
            initial = cognitive_support_snapshot(instrument, run.result.get('dimension_scores', {}))
            initial['diagnostic_id'] = run.public_id
            break
    attempts = (await db.execute(select(TTAttempt).join(TTItem, TTAttempt.item_id == TTItem.id).where(
        TTAttempt.org_id == ctx.org_id, TTAttempt.user_id == ctx.user.id, TTItem.org_id == ctx.org_id,
        TTItem.program_id == profile.program_id, TTAttempt.content_approved == True,
        TTAttempt.assisted == False, TTAttempt.unseen_family == True,
        TTAttempt.phase.in_(['independent', 'review'])).order_by(TTAttempt.created_at.desc(), TTAttempt.id.desc()))).scalars().all()
    performance = {}
    for attempt in attempts:
        if attempt.skill_id in performance:
            continue
        evaluation = await latest_evaluation(db, attempt.id)
        if evaluation and evaluation.status == 'scored' and evaluation.score is not None:
            performance[attempt.skill_id] = {'source': 'independent_performance', 'attempt_id': attempt.public_id,
                'evaluation_revision': evaluation.revision,
                'explanation': 'Observed independent performance now guides this skill; initial assessment limits no longer apply.'}
    return initial, performance


def support_for_skill(initial, performance, skill_id):
    return dict(performance.get(skill_id, initial))


def supported_minutes(requested, remaining_budget, support, initial_minutes_used, *, divisible=False):
    available = max(0, remaining_budget)
    if support.get('source') == 'initial_assessment':
        recommendations = support['recommendations']
        available = min(available, recommendations['chunk_minutes'],
                        max(0, recommendations['new_material_minutes']-initial_minutes_used))
    return min(requested, available) if divisible else requested if requested <= available else 0
