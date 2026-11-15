"""Authored coached practice and a fresh same-principle case after an error."""
from fastapi import HTTPException

PRACTICE_MODES = {'case', 'retell', 'think_first'}


def practice_mode(item):
    rubric = item.rubric if isinstance(item.rubric, dict) else {}
    return rubric.get('practice_mode', 'case')


def validate_practice_metadata(data):
    rubric = data.get('rubric', {})
    if not isinstance(rubric, dict):
        raise HTTPException(422, 'Item rubric must be an object')
    mode = rubric.get('practice_mode', 'case')
    if not isinstance(mode, str) or mode not in PRACTICE_MODES:
        raise HTTPException(422, 'Practice mode must be case, retell or think_first')
    if mode == 'case':
        return
    if data.get('kind') != 'reasoning':
        raise HTTPException(422, 'Retell and ThinkFirst require expert-authored reasoning rubrics')
    field = 'source_material' if mode == 'retell' else 'ai_answer'
    material = rubric.get(field)
    if not isinstance(material, str) or not 1 <= len(material.strip()) <= 30000:
        raise HTTPException(422, f'{mode} requires expert-authored {field} of 1–30000 characters')


def safe_practice_metadata(item):
    mode = practice_mode(item)
    result = {'practice_mode': mode}
    if mode in {'retell', 'think_first'}:
        field = 'source_material' if mode == 'retell' else 'ai_answer'
        result[field] = item.rubric.get(field, '')
    return result


async def followup_target(db, org_id, user_id, program_id):
    from sqlmodel import select
    from src.db.thinktogether.learning import TTAttempt, TTItem
    from src.services.thinktogether.learning_evidence import latest_evaluation
    attempts = (await db.execute(select(TTAttempt).join(TTItem, TTAttempt.item_id == TTItem.id).where(
        TTAttempt.org_id == org_id, TTAttempt.user_id == user_id, TTItem.org_id == org_id,
        TTItem.program_id == program_id).order_by(TTAttempt.created_at.desc(), TTAttempt.id.desc()))).scalars().all()
    for attempt in attempts:
        if attempt.phase == 'coached':
            return attempt.skill_id, 'same_principle_after_coached_practice'
        if attempt.phase not in {'independent', 'review'} or not attempt.content_approved or attempt.assisted or not attempt.unseen_family:
            continue
        evaluation = await latest_evaluation(db, attempt.id)
        if evaluation and evaluation.status == 'scored' and evaluation.score is not None:
            # This is the existing criterion success boundary, never a cognitive norm.
            return (attempt.skill_id, 'same_principle_after_error') if evaluation.score < .8 else (None, 'plan_selection')
    return None, 'plan_selection'
