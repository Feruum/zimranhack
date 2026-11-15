"""Portable curriculum packages, lessons and held-out timed examination forms."""
from datetime import timedelta
from fastapi import HTTPException

from src.db.thinktogether.learning import (TTProgram, TTSkill, TTItem, TTLesson,
    TTMockForm, TTMockRun, TTDelivery, TTAttempt, TTContentAudit, TTLessonProgress)
from src.services.thinktogether.common import utcnow, as_utc, require_permission
from src.services.thinktogether.learning_content import rows, find, public, validate_item, issue_item, record_exposure
from src.services.thinktogether.learning_evidence import learner_lock, latest_evaluation, submit_attempt


BLUEPRINT = 'PMP-2026-07'
WEIGHTS = {'People': 33, 'Process': 41, 'Business environment': 26}
SOURCE = 'https://www.pmi.org/certifications/project-management-pmp'
TASKS = {f'{domain}.{task}' for domain, count in [('people', 8), ('process', 10), ('business', 8)] for task in range(1, count+1)}


def validate_package(package):
    try:
        return _validate_package(package)
    except (TypeError, ValueError, AttributeError, KeyError):
        return {'valid': False, 'errors': ['Malformed curriculum package fields or references'], 'warnings': []}


def _validate_package(package):
    errors, warnings = [], []
    if not isinstance(package, dict):
        return {'valid': False, 'errors': ['Package must be an object'], 'warnings': []}
    program = package.get('program', {})
    skills, items, lessons, forms = (package.get(k, []) for k in ('skills', 'items', 'lessons', 'mock_forms'))
    if not isinstance(program, dict) or any(not isinstance(x, list) for x in (skills, items, lessons, forms)):
        return {'valid': False, 'errors': ['Invalid package structure'], 'warnings': []}
    if any(not isinstance(entry, dict) for collection in (skills, items, lessons, forms) for entry in collection):
        return {'valid': False, 'errors': ['Every skill, item, lesson and form must be an object'], 'warnings': []}
    if not program.get('title', '').strip():
        errors.append('A program title is required')
    if not isinstance(program.get('curriculum', {}), dict):
        return {'valid': False, 'errors': ['Curriculum must be an object'], 'warnings': []}
    if program.get('blueprint_version') != BLUEPRINT:
        errors.append(f'Blueprint must be {BLUEPRINT}')
    if program.get('curriculum', {}).get('domain_weights') != WEIGHTS:
        errors.append('Blueprint domain weights must be People 33, Process 41, Business environment 26')
    codes = [s.get('code') for s in skills]
    if not skills or len(codes) != len(set(codes)) or any(not c for c in codes):
        errors.append('Unique nonempty skill codes are required')
    graph = {s.get('code'): s.get('prerequisites', []) for s in skills}
    active, done = set(), set()
    def visit(code):
        if code in active or code not in graph:
            raise ValueError()
        if code in done:
            return
        active.add(code)
        for parent in graph[code]:
            visit(parent)
        active.remove(code)
        done.add(code)
    try:
        for code in graph:
            visit(code)
    except (ValueError, TypeError):
        errors.append('Prerequisite graph must be acyclic and reference package skills')
    item_codes = [i.get('code') for i in items]
    if len(item_codes) != len(set(item_codes)) or any(not c for c in item_codes):
        errors.append('Unique item codes are required')
    by_item = {i.get('code'): i for i in items}
    for item in items:
        if item.get('skill_code') not in graph:
            errors.append(f'Unknown skill on item {item.get("code")}')
        try:
            validate_item(item)
        except (HTTPException, ValueError, TypeError) as exc:
            errors.append(f'Invalid item {item.get("code")}: {getattr(exc, "detail", str(exc))}')
    for lesson in lessons:
        if lesson.get('skill_code') not in graph or not lesson.get('body_markdown', '').strip() or not lesson.get('title', '').strip():
            errors.append('Each lesson needs a known skill, title and substantive authored text')
    heldout, seen_families = set(), set()
    for form in forms:
        ids = form.get('item_codes', [])
        if len(ids) != len(set(ids)) or any(i not in by_item for i in ids):
            errors.append('Mock forms must contain unique known item codes')
        families = {by_item[i].get('family_id') for i in ids if i in by_item}
        if len(families) != len(ids):
            errors.append('Each mock question must use a distinct case family')
        if families & seen_families:
            errors.append('Mock forms must use distinct held-out families')
        seen_families |= families
        heldout.update(ids)
        if program.get('full_curriculum'):
            if len(ids) != 180 or form.get('duration_minutes', 240) != 240:
                errors.append('Full mock forms require 180 items and a 240 minute server-enforced timer')
            counts = {d: sum(1 for i in ids if i in by_item and next((s.get('domain') for s in skills if s.get('code') == by_item[i].get('skill_code')), '') == d) for d in WEIGHTS}
            if any(abs(counts[d]/180*100-weight) > 1 for d, weight in WEIGHTS.items()):
                errors.append('Mock form domain balance must match the 2026 blueprint within rounding')
    if program.get('full_curriculum'):
        coverage = set(program.get('curriculum', {}).get('task_coverage', []))
        if not TASKS <= coverage:
            errors.append('A full curriculum must map all 26 blueprint tasks')
        if len(forms) < 2:
            errors.append('Two distinct held-out mock examination forms are required')
        for code in graph:
            if not any(l.get('skill_code') == code for l in lessons):
                errors.append(f'Authored lesson missing for {code}')
            lesson_families = {family for lesson in lessons for family in lesson.get('case_families', [])}
            families = {i.get('family_id') for i in items if i.get('skill_code') == code and i.get('family_id') not in seen_families | lesson_families
                        and i.get('rubric', {}).get('practice_mode', 'case') == 'case'}
            if len(families) < 3:
                errors.append(f'{code} needs three practice families separate from mock examinations')
    else:
        warnings.append('Draft/sample material is not an approved full preparation course.')
    return {'valid': not errors, 'errors': errors, 'warnings': warnings,
            'blueprint_source': SOURCE, 'counts': {'skills': len(skills), 'items': len(items), 'lessons': len(lessons), 'mock_forms': len(forms)}}


async def import_package(db, ctx, package):
    require_permission(ctx, 'content')
    report = validate_package(package)
    if not report['valid']:
        raise HTTPException(422, report)
    source = package['program']
    program = TTProgram(org_id=ctx.org_id, title=source['title'], description=source.get('description', ''),
        blueprint_version=source['blueprint_version'], full_curriculum=source.get('full_curriculum', False),
        is_sample=source.get('is_sample', True), curriculum=source.get('curriculum', {}))
    db.add(program)
    await db.flush()
    skill_map = {}
    for s in package['skills']:
        skill = TTSkill(org_id=ctx.org_id, program_id=program.id, code=s['code'], title=s['title'],
            domain=s.get('domain', ''), description=s.get('description', ''), position=s.get('position', 0))
        db.add(skill)
        await db.flush()
        skill_map[s['code']] = skill
    for s in package['skills']:
        skill_map[s['code']].prerequisites = [skill_map[p].id for p in s.get('prerequisites', [])]
        db.add(skill_map[s['code']])
    item_map = {}
    for i in package['items']:
        fields = {k: i[k] for k in ('family_id', 'title', 'prompt', 'kind', 'choices', 'answer_key', 'rubric',
                                   'explanation', 'hints', 'estimated_minutes', 'difficulty') if k in i}
        item = TTItem(org_id=ctx.org_id, program_id=program.id, skill_id=skill_map[i['skill_code']].id,
                      is_sample=program.is_sample, **fields)
        db.add(item)
        await db.flush()
        item_map[i['code']] = item
    for lesson in package.get('lessons', []):
        db.add(TTLesson(org_id=ctx.org_id, program_id=program.id, skill_id=skill_map[lesson['skill_code']].id,
            title=lesson['title'], body_markdown=lesson['body_markdown'], objectives=lesson.get('objectives', []),
            case_families=lesson.get('case_families', []),
            estimated_minutes=lesson.get('estimated_minutes', 10), position=lesson.get('position', 0), is_sample=program.is_sample))
    for form in package.get('mock_forms', []):
        db.add(TTMockForm(org_id=ctx.org_id, program_id=program.id, title=form['title'], form_code=form['form_code'],
            item_ids=[item_map[i].id for i in form['item_codes']], duration_minutes=form.get('duration_minutes', 240), is_sample=program.is_sample))
    db.add(TTContentAudit(org_id=ctx.org_id, actor_id=ctx.user.id, resource='packages', resource_id=program.public_id,
                          previous_status='', status='draft', evidence='Validated JSON package import; approval not implied'))
    await db.commit()
    return {'program': public(program, staff=True), 'validation': report}


async def export_package(db, ctx, program_id):
    require_permission(ctx, 'content')
    program = await find(db, TTProgram, ctx.org_id, program_id)
    skills = await rows(db, TTSkill, TTSkill.org_id == ctx.org_id, TTSkill.program_id == program.id)
    items = await rows(db, TTItem, TTItem.org_id == ctx.org_id, TTItem.program_id == program.id)
    lessons = await rows(db, TTLesson, TTLesson.org_id == ctx.org_id, TTLesson.program_id == program.id)
    forms = await rows(db, TTMockForm, TTMockForm.org_id == ctx.org_id, TTMockForm.program_id == program.id)
    codes = {s.id: s.code for s in skills}
    item_codes = {i.id: i.public_id for i in items}
    result = {'schema_version': 1, 'program': public(program),
        'skills': [dict(public(s), prerequisites=[codes[p] for p in s.prerequisites], code=s.code) for s in skills],
        'items': [dict(public(i), code=i.public_id, skill_code=codes[i.skill_id]) for i in items],
        'lessons': [dict(public(l), skill_code=codes[l.skill_id]) for l in lessons],
        'mock_forms': [dict(public(f), item_codes=[item_codes[i] for i in f.item_ids]) for f in forms]}
    result['validation'] = validate_package(result)
    return result


async def create_lesson(db, ctx, data):
    require_permission(ctx, 'content')
    program = await find(db, TTProgram, ctx.org_id, data['program_id'])
    skill = await find(db, TTSkill, ctx.org_id, data['skill_id'])
    if program.status == 'published':
        raise HTTPException(409, 'Published program content is frozen; create a new program version')
    if skill.program_id != program.id or not data.get('body_markdown', '').strip() or not data.get('title', '').strip():
        raise HTTPException(422, 'A lesson requires a program skill, title and authored text')
    fields = {k: data[k] for k in ('title', 'body_markdown', 'objectives', 'case_families', 'estimated_minutes', 'position', 'is_sample') if k in data}
    if data.get('previous_version_id'):
        prior = await find(db, TTLesson, ctx.org_id, data['previous_version_id'], lock=True)
        versions = await rows(db, TTLesson, TTLesson.org_id == ctx.org_id, TTLesson.series_id == prior.series_id)
        fields.update(series_id=prior.series_id, version=max(l.version for l in versions)+1)
    lesson = TTLesson(org_id=ctx.org_id, program_id=program.id, skill_id=skill.id, **fields)
    db.add(lesson)
    await db.commit()
    return public(lesson, staff=True)


async def lessons(db, ctx, program_id, lesson_id=None):
    program = await find(db, TTProgram, ctx.org_id, program_id)
    if not ctx.can('content') and (program.status != 'published' or program.is_sample):
        raise HTTPException(404, 'Program not available')
    found = await rows(db, TTLesson, TTLesson.org_id == ctx.org_id, TTLesson.program_id == program.id)
    found = [l for l in found if ctx.can('content') or (l.status == 'published' and not l.is_sample)]
    progress = await rows(db, TTLessonProgress, TTLessonProgress.org_id == ctx.org_id,
                          TTLessonProgress.user_id == ctx.user.id,
                          TTLessonProgress.lesson_id.in_([lesson.id for lesson in found]))
    completed = {row.lesson_id: as_utc(row.completed_at).isoformat() for row in progress}

    def lesson_payload(lesson):
        return {**public(lesson), 'completed': lesson.id in completed,
                'completed_at': completed.get(lesson.id)}

    if lesson_id:
        selected = next((l for l in found if l.public_id == lesson_id), None)
        if not selected:
            raise HTTPException(404, 'Lesson not found')
        for family in selected.case_families:
            await record_exposure(db, ctx.org_id, ctx.user.id, family, 'lesson', selected.public_id)
        await db.commit()
        return lesson_payload(selected)
    return [{k: v for k, v in lesson_payload(l).items() if k not in {'body_markdown', 'case_families'}} for l in sorted(found, key=lambda l: (l.position, l.id))]


async def complete_lesson(db, ctx, lesson_id):
    lesson = await find(db, TTLesson, ctx.org_id, lesson_id)
    if not ctx.can('content') and (lesson.status != 'published' or lesson.is_sample):
        raise HTTPException(404, 'Lesson not available')
    await learner_lock(db, ctx.org_id, ctx.user.id)
    existing = await rows(db, TTLessonProgress, TTLessonProgress.org_id == ctx.org_id,
                          TTLessonProgress.user_id == ctx.user.id, TTLessonProgress.lesson_id == lesson.id)
    progress = existing[0] if existing else TTLessonProgress(org_id=ctx.org_id, user_id=ctx.user.id, lesson_id=lesson.id)
    db.add(progress)
    await db.commit()
    return {'lesson_id': lesson.public_id, 'completed_at': as_utc(progress.completed_at).isoformat(), 'mastery_evidence': False}


async def mock_forms(db, ctx, program_id):
    program = await find(db, TTProgram, ctx.org_id, program_id)
    forms = await rows(db, TTMockForm, TTMockForm.org_id == ctx.org_id, TTMockForm.program_id == program.id)
    return [{'id': f.public_id, 'title': f.title, 'form_code': f.form_code, 'duration_minutes': f.duration_minutes,
             'question_count': len(f.item_ids), 'is_sample': f.is_sample, 'status': f.status} for f in forms
            if ctx.can('content') or (f.status == 'published' and not f.is_sample)]


async def start_mock(db, ctx, form_id):
    form = await find(db, TTMockForm, ctx.org_id, form_id)
    if (form.status != 'published' or form.is_sample) and not ctx.can('content'):
        raise HTTPException(404, 'Mock form not available')
    if not ctx.can('content'):
        from src.services.thinktogether.learning import learning_readiness
        if not (await learning_readiness(db, ctx.org_id, ctx.user.id))['ready']:
            raise HTTPException(409, 'Complete required diagnostics first')
    await learner_lock(db, ctx.org_id, ctx.user.id)
    existing = await rows(db, TTMockRun, TTMockRun.org_id == ctx.org_id, TTMockRun.user_id == ctx.user.id,
                          TTMockRun.form_id == form.id, TTMockRun.status == 'in_progress')
    if existing:
        return await mock_detail(db, ctx, existing[0].public_id)
    run = TTMockRun(org_id=ctx.org_id, user_id=ctx.user.id, form_id=form.id,
                    deadline=utcnow()+timedelta(minutes=form.duration_minutes))
    db.add(run)
    await db.flush()
    deliveries = {}
    for iid in form.item_ids:
        item = await db.get(TTItem, iid)
        if not item or item.org_id != ctx.org_id:
            raise HTTPException(409, 'Mock form has an unavailable item')
        issued = await issue_item(db, ctx, item, phase='mock')
        delivery = await find(db, TTDelivery, ctx.org_id, issued['delivery_id'])
        delivery.expires_at = run.deadline
        delivery.mock_run_id = run.id
        db.add(delivery)
        deliveries[item.public_id] = delivery.public_id
    run.deliveries = deliveries
    db.add(run)
    await db.commit()
    return await mock_detail(db, ctx, run.public_id)


async def mock_detail(db, ctx, identifier):
    run = await find(db, TTMockRun, ctx.org_id, identifier)
    if run.user_id != ctx.user.id:
        raise HTTPException(404, 'Mock run not found')
    if run.status == 'in_progress' and utcnow() > as_utc(run.deadline):
        return await finalize_mock(db, ctx, identifier)
    form = await db.get(TTMockForm, run.form_id)
    from src.services.thinktogether.learning_content import safe_item
    questions = []
    for iid in form.item_ids:
        item = await db.get(TTItem, iid)
        answered = await rows(db, TTAttempt, TTAttempt.org_id == ctx.org_id, TTAttempt.user_id == ctx.user.id,
                              TTAttempt.delivery_id == (await find(db, TTDelivery, ctx.org_id, run.deliveries[item.public_id])).id)
        question = {'item': safe_item(item), 'answered': bool(answered), 'delivery_id': run.deliveries[item.public_id]}
        if answered:
            question['response'] = answered[0].response
        if run.status == 'completed':
            question.update(answer_key=item.answer_key, explanation=item.explanation)
        questions.append(question)
    return {'id': run.public_id, 'status': run.status, 'deadline': as_utc(run.deadline).isoformat(),
            'score': run.score, 'result': run.result, 'questions': questions,
            'answered': len(run.attempt_ids), 'total': len(form.item_ids)}


async def answer_mock(db, ctx, run_id, item_id, response, idempotency_key):
    run = await find(db, TTMockRun, ctx.org_id, run_id, lock=True)
    if run.user_id != ctx.user.id:
        raise HTTPException(404, 'Mock run not found')
    if run.status != 'in_progress' or utcnow() > as_utc(run.deadline):
        raise HTTPException(409, 'Mock run is closed or its deadline has passed')
    if item_id not in run.deliveries:
        raise HTTPException(404, 'Question not in this form')
    result = await submit_attempt(db, ctx, item_id, response, idempotency_key, delivery_id=run.deliveries[item_id])
    # submit_attempt commits; re-lock the run and project its authoritative deliveries.
    run = await find(db, TTMockRun, ctx.org_id, run_id, lock=True)
    attempts = []
    for did in run.deliveries.values():
        delivery = await find(db, TTDelivery, ctx.org_id, did)
        attempts.extend(await rows(db, TTAttempt, TTAttempt.delivery_id == delivery.id))
    run.attempt_ids = [a.public_id for a in attempts]
    db.add(run)
    await db.commit()
    # No score, feedback or answer key before the entire form is finalized.
    return {'id': result['id'], 'status': 'submitted', 'item_id': item_id, 'response': response}


async def finalize_mock(db, ctx, identifier):
    run = await find(db, TTMockRun, ctx.org_id, identifier, lock=True)
    if run.user_id != ctx.user.id:
        raise HTTPException(404, 'Mock run not found')
    form = await db.get(TTMockForm, run.form_id)
    if run.status == 'completed':
        return await mock_detail(db, ctx, identifier)
    attempts = []
    for did in run.deliveries.values():
        delivery = await find(db, TTDelivery, ctx.org_id, did)
        attempts.extend(await rows(db, TTAttempt, TTAttempt.delivery_id == delivery.id))
    evaluations = [await latest_evaluation(db, a.id) for a in attempts]
    pending = any(e is None or e.score is None for e in evaluations)
    run.attempt_ids = [a.public_id for a in attempts]
    run.status = 'needs_review' if pending else 'completed'
    run.score = None if pending else sum(e.score for e in evaluations)/len(form.item_ids) if form.item_ids else 0
    run.result = {'answered': len(attempts), 'unanswered': len(form.item_ids)-len(attempts),
                  'notice': 'Practice criterion result; no official PMP pass prediction.'}
    run.completed_at = utcnow()
    db.add(run)
    await db.commit()
    return await mock_detail(db, ctx, identifier)


async def refresh_mock_evaluation(db, attempt):
    """Replays a closed mock report after a delayed grade or expert correction."""
    if not attempt.delivery_id or attempt.phase != 'mock':
        return
    delivery = await db.get(TTDelivery, attempt.delivery_id)
    run = await db.get(TTMockRun, delivery.mock_run_id) if delivery and delivery.mock_run_id else None
    if not run or run.status == 'in_progress':
        return
    form = await db.get(TTMockForm, run.form_id)
    attempts = []
    for did in run.deliveries.values():
        delivered = await find(db, TTDelivery, run.org_id, did)
        attempts.extend(await rows(db, TTAttempt, TTAttempt.delivery_id == delivered.id))
    evaluations = [await latest_evaluation(db, a.id) for a in attempts]
    pending = any(e is None or e.score is None for e in evaluations)
    run.status = 'needs_review' if pending else 'completed'
    run.score = None if pending else sum(e.score for e in evaluations)/len(form.item_ids) if form.item_ids else 0
    db.add(run)
    await db.flush()
