"""ThinkTogether private onboarding, diagnostics and adaptive Today projection."""
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlmodel import select

from src.db.thinktogether.learning import (TTProgram, TTSkill, TTItem, TTInstrument,
    TTLearnerProfile, TTConsent, TTDiagnosticRun, TTAttempt, TTExposure, TTMastery,
    TTReview, TTPlan, TTSupportCase, TTLesson, TTLessonProgress)
from src.services.thinktogether.common import utcnow, as_utc
from src.services.thinktogether.learning_content import (
    rows, find, public, list_programs, get_item, create_program, create_skill,
    create_item, create_instrument, transition_content, launch_readiness,
    seed_sample, issue_item, hint, record_exposure,
)
from src.services.thinktogether.learning_evidence import (
    submit_attempt, attempt_detail, expert_evaluate, rebuild_evidence,
    latest_evaluation, grade_pending_attempt, learner_lock, learning_grade_job,
)


async def profile_row(db, org_id, user_id):
    profiles = await rows(db, TTLearnerProfile, TTLearnerProfile.org_id == org_id, TTLearnerProfile.user_id == user_id)
    return profiles[0] if profiles else None


async def get_profile(db, ctx):
    profile = await profile_row(db, ctx.org_id, ctx.user.id)
    result = public(profile) if profile else {'goal': '', 'exam_date': '', 'minutes_per_day': 30,
        'timezone': 'UTC', 'language': 'en', 'schedule_slots': [], 'domain_profile': {}, 'cognitive_profile': {}}
    if profile and profile.program_id:
        program = await db.get(TTProgram, profile.program_id)
        result['program_id'] = program.public_id if program else None
    result['readiness'] = await learning_readiness(db, ctx.org_id, ctx.user.id)
    result['assessment_notice'] = 'Private criterion scores from an approved instrument; no intelligence labels or population norms are inferred.'
    return result


async def save_profile(db, ctx, data):
    await learner_lock(db, ctx.org_id, ctx.user.id)
    profile = await profile_row(db, ctx.org_id, ctx.user.id)
    profile = profile or TTLearnerProfile(org_id=ctx.org_id, user_id=ctx.user.id)
    if 'minutes_per_day' in data and not 5 <= int(data['minutes_per_day']) <= 240:
        raise HTTPException(422, 'Daily budget must be between 5 and 240 minutes')
    try:
        learner_zone = ZoneInfo(data.get('timezone') or profile.timezone or 'UTC')
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, 'Unknown timezone')
    if data.get('exam_date'):
        try:
            exam = date.fromisoformat(data['exam_date'])
            if exam < utcnow().astimezone(learner_zone).date():
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Choose a current or future exam date in your timezone')
    if data.get('program_id'):
        program = await find(db, TTProgram, ctx.org_id, data['program_id'])
        if profile.program_id and profile.program_id != program.id:
            profile.domain_profile = {}
        profile.program_id = program.id
    for key in ('goal', 'exam_date', 'minutes_per_day', 'timezone', 'language', 'schedule_slots'):
        if key in data:
            setattr(profile, key, data[key])
    profile.updated_at = utcnow()
    db.add(profile)
    await db.commit()
    return await get_profile(db, ctx)


async def consent_current(db, org_id, user_id, instrument):
    consents = await rows(db, TTConsent, TTConsent.org_id == org_id, TTConsent.user_id == user_id,
                           TTConsent.instrument_id == instrument.id, TTConsent.version == instrument.consent_version)
    return bool(consents and sorted(consents, key=lambda c: c.id)[-1].accepted)


async def give_consent(db, ctx, instrument_id, accepted, version):
    instrument = await find(db, TTInstrument, ctx.org_id, instrument_id)
    if instrument.status != 'published' or instrument.is_sample:
        raise HTTPException(409, 'Instrument is not approved for use')
    if version != instrument.consent_version:
        raise HTTPException(409, 'Consent text has changed; review the current version')
    consent = TTConsent(org_id=ctx.org_id, user_id=ctx.user.id, instrument_id=instrument.id,
                        version=version, accepted=accepted, text_snapshot=instrument.consent_text)
    db.add(consent)
    await db.commit()
    return {'id': consent.public_id, 'accepted': accepted, 'version': version}


async def learning_readiness(db, org_id, user_id):
    profile = await profile_row(db, org_id, user_id)
    complete = bool(profile and profile.goal.strip() and profile.exam_date and profile.program_id
                    and profile.minutes_per_day >= 5 and profile.timezone)
    domain, cognitive = False, False
    if profile:
        runs = await rows(db, TTDiagnosticRun, TTDiagnosticRun.org_id == org_id,
                          TTDiagnosticRun.user_id == user_id, TTDiagnosticRun.status == 'completed')
        for run in runs:
            instrument = await db.get(TTInstrument, run.instrument_id)
            approved = bool(instrument and instrument.status == 'published' and not instrument.is_sample
                            and instrument.approval_evidence and await consent_current(db, org_id, user_id, instrument))
            if approved and run.kind == 'domain' and run.program_id == profile.program_id:
                domain = True
            if approved and run.kind == 'cognitive' and instrument.license_reference:
                cognitive = True
    return {'profile_complete': complete, 'domain_diagnostic_complete': domain,
            'cognitive_complete': cognitive, 'ready': complete and domain and cognitive}


async def diagnostic_options(db, ctx):
    instruments = await rows(db, TTInstrument, TTInstrument.org_id == ctx.org_id,
                             TTInstrument.status == 'published', TTInstrument.is_sample == False)
    runs = await rows(db, TTDiagnosticRun, TTDiagnosticRun.org_id == ctx.org_id, TTDiagnosticRun.user_id == ctx.user.id)
    return {'instruments': [{'id': i.public_id, 'title': i.title, 'version': i.version, 'kind': i.kind,
        'consent_version': i.consent_version, 'consent_text': i.consent_text,
        'consented': await consent_current(db, ctx.org_id, ctx.user.id, i)} for i in instruments],
        'runs': [{k: v for k, v in public(r).items() if k not in {'item_ids', 'instrument_id'}} for r in runs],
        'readiness': await learning_readiness(db, ctx.org_id, ctx.user.id)}


async def start_diagnostic(db, ctx, kind, instrument_id=None):
    if kind not in {'domain', 'cognitive'}:
        raise HTTPException(422, 'Choose domain or cognitive diagnostics')
    profile = await profile_row(db, ctx.org_id, ctx.user.id)
    if not profile or not profile.goal.strip() or not profile.program_id:
        raise HTTPException(409, 'Complete your learning goal and program first')
    instruments = await rows(db, TTInstrument, TTInstrument.org_id == ctx.org_id,
                             TTInstrument.kind == kind, TTInstrument.status == 'published', TTInstrument.is_sample == False)
    if instrument_id:
        instruments = [i for i in instruments if i.public_id == instrument_id]
    if not instruments:
        raise HTTPException(409, 'An expert-approved instrument is required before this diagnostic can start')
    instrument = sorted(instruments, key=lambda i: (i.version, i.id))[-1]
    if not instrument.approval_evidence or not instrument.license_reference:
        raise HTTPException(409, 'Instrument licensing and expert approval are required')
    if not await consent_current(db, ctx.org_id, ctx.user.id, instrument):
        raise HTTPException(409, 'Review and accept the current instrument consent first')
    item_ids = instrument.definition.get('item_ids', [])
    if not item_ids:
        raise HTTPException(409, 'Approved instrument has no items')
    for iid in item_ids:
        item = await db.get(TTItem, iid)
        if not item or item.org_id != ctx.org_id or item.status != 'published' or item.is_sample or (kind == 'domain' and item.program_id != profile.program_id):
            raise HTTPException(409, 'Instrument items are not available for this program')
    await learner_lock(db, ctx.org_id, ctx.user.id)
    existing = await rows(db, TTDiagnosticRun, TTDiagnosticRun.org_id == ctx.org_id,
                          TTDiagnosticRun.user_id == ctx.user.id, TTDiagnosticRun.instrument_id == instrument.id)
    # Starting/resuming is idempotent for a licensed instrument version. Retakes
    # require a specialist-approved new version with appropriate fresh items.
    run = max(existing, key=lambda r:r.id) if existing else TTDiagnosticRun(org_id=ctx.org_id, user_id=ctx.user.id,
        program_id=profile.program_id, instrument_id=instrument.id, kind=kind, item_ids=item_ids)
    db.add(run)
    await db.commit()
    return await diagnostic_next(db, ctx, run.public_id)


async def diagnostic_next(db, ctx, identifier):
    run = await find(db, TTDiagnosticRun, ctx.org_id, identifier)
    if run.user_id != ctx.user.id:
        raise HTTPException(404, 'Diagnostic not found')
    instrument = await db.get(TTInstrument, run.instrument_id)
    if run.status == 'in_progress' and not await consent_current(db, ctx.org_id, ctx.user.id, instrument):
        raise HTTPException(409, 'Current assessment consent is required')
    attempts = await rows(db, TTAttempt, TTAttempt.diagnostic_id == run.id)
    answered = {a.item_id for a in attempts}
    result = {'run': public(run), 'answered': len(answered), 'total': len(run.item_ids)}
    # Internal item IDs and the scoring definition stay server-side.
    result['run'].pop('item_ids', None)
    result['run'].pop('instrument_id', None)
    remaining = [i for i in run.item_ids if i not in answered]
    if run.status == 'in_progress' and remaining:
        item = await db.get(TTItem, remaining[0])
        result.update(await issue_item(db, ctx, item, phase='diagnostic', diagnostic_id=run.id))
        await db.commit()
    return result


async def refresh_diagnostic(db, diagnostic_id):
    run = await db.get(TTDiagnosticRun, diagnostic_id)
    instrument = await db.get(TTInstrument, run.instrument_id)
    attempts = await rows(db, TTAttempt, TTAttempt.diagnostic_id == run.id)
    by_item = {a.item_id: a for a in attempts}
    if any(i not in by_item for i in run.item_ids):
        return
    evaluations = {i: await latest_evaluation(db, by_item[i].id) for i in run.item_ids}
    if any(e is None or e.status != 'scored' or e.score is None for e in evaluations.values()) or any(a.assisted or not a.unseen_family for a in attempts):
        run.status = 'needs_review'
        db.add(run)
        return
    if instrument.definition.get('scoring') != 'dimension_mean':
        run.status = 'needs_review'
        db.add(run)
        return
    # Definitions contain explicit item-to-dimension rules approved by an expert.
    dimensions = instrument.definition.get('dimensions', {'criterion': run.item_ids})
    scores = {}
    for dimension, ids in dimensions.items():
        applicable = [evaluations[int(i)].score for i in ids if int(i) in evaluations]
        if applicable:
            scores[dimension] = sum(applicable)/len(applicable)
    run.result = {'dimension_scores': scores, 'instrument_version': instrument.version,
                  'interpretation': 'Criterion scores only; not population norms or a clinical diagnosis.'}
    run.status, run.completed_at = 'completed', utcnow()
    profile = await profile_row(db, run.org_id, run.user_id)
    if profile:
        if run.kind == 'cognitive':
            from src.services.thinktogether.learning_support import cognitive_support_snapshot
            run.result = dict(run.result, support=cognitive_support_snapshot(instrument, scores))
            profile.cognitive_profile = dict(run.result, diagnostic_id=run.public_id)
        else:
            profile.domain_profile = dict(run.result, diagnostic_id=run.public_id)
        db.add(profile)
    db.add(run)
    await db.flush()


async def skill_map(db, ctx):
    profile = await profile_row(db, ctx.org_id, ctx.user.id)
    if not profile or not profile.program_id:
        return []
    skills = await rows(db, TTSkill, TTSkill.org_id == ctx.org_id, TTSkill.program_id == profile.program_id)
    masters = await rows(db, TTMastery, TTMastery.org_id == ctx.org_id, TTMastery.user_id == ctx.user.id)
    by_skill, by_id = {m.skill_id: m for m in masters}, {s.id: s for s in skills}
    return [{'id': s.public_id, 'code': s.code, 'title': s.title, 'domain': s.domain,
        'description': s.description, 'prerequisites': [by_id[i].public_id for i in s.prerequisites if i in by_id],
        'state': by_skill[s.id].state if s.id in by_skill else 'new',
        'independent_days': by_skill[s.id].independent_days if s.id in by_skill else 0,
        'independent_families': by_skill[s.id].independent_families if s.id in by_skill else 0,
        'evidence_ids': by_skill[s.id].evidence_ids if s.id in by_skill else []} for s in sorted(skills, key=lambda s: (s.position, s.id))]


async def unseen_items(db, ctx, program_id, *, include_coached=False):
    items = await rows(db, TTItem, TTItem.org_id == ctx.org_id, TTItem.program_id == program_id,
                       TTItem.status == 'published', TTItem.is_sample == False)
    attempted = {a.family_id for a in await rows(db, TTAttempt, TTAttempt.org_id == ctx.org_id, TTAttempt.user_id == ctx.user.id)}
    exposed = {e.family_id for e in await rows(db, TTExposure, TTExposure.org_id == ctx.org_id, TTExposure.user_id == ctx.user.id)}
    from src.services.thinktogether.learning_content import practice_reserved_families
    reserved_content = await practice_reserved_families(db, ctx.org_id, program_id)
    from src.db.thinktogether.learning import TTDelivery
    reserved = (await db.execute(select(TTItem.family_id).join(TTDelivery, TTDelivery.item_id == TTItem.id).where(
        TTDelivery.org_id == ctx.org_id, TTDelivery.user_id == ctx.user.id, TTDelivery.room_id != None))).scalars().all()
    from src.services.thinktogether.learning_mechanics import practice_mode
    return [i for i in items if i.family_id not in attempted | exposed | reserved_content | set(reserved)
            and (include_coached or practice_mode(i) == 'case')]


async def shortage(db, ctx, program_id, reason, skill_ids):
    key = f'item-shortage:{program_id}:{"-".join(str(s) for s in sorted(skill_ids))}'
    existing = await rows(db, TTSupportCase, TTSupportCase.org_id == ctx.org_id,
                          TTSupportCase.user_id == ctx.user.id, TTSupportCase.dedup_key == key)
    if existing:
        return existing[0]
    case = TTSupportCase(org_id=ctx.org_id, user_id=ctx.user.id, kind='item_shortage', dedup_key=key,
                         details={'program_id': program_id, 'reason': reason, 'skill_ids': skill_ids})
    db.add(case)
    await db.flush()
    return case


async def build_plan(db, ctx, budget_minutes=None):
    await learner_lock(db, ctx.org_id, ctx.user.id)
    profile = await profile_row(db, ctx.org_id, ctx.user.id)
    budget = budget_minutes or (profile.minutes_per_day if profile else 30)
    if not 5 <= budget <= 240:
        raise HTTPException(422, 'Time budget must be between 5 and 240 minutes')
    today = utcnow().astimezone(ZoneInfo(profile.timezone if profile else 'UTC')).date().isoformat()
    previous = await rows(db, TTPlan, TTPlan.org_id == ctx.org_id, TTPlan.user_id == ctx.user.id, TTPlan.plan_date == today)
    plan = TTPlan(org_id=ctx.org_id, user_id=ctx.user.id, program_id=profile.program_id if profile else None,
                  plan_date=today, version=max([p.version for p in previous], default=0)+1, budget_minutes=budget)
    from src.services.thinktogether.learning_deadline import deadline_schedule
    deadline = await deadline_schedule(db, ctx, profile, today_budget=budget)
    items = await unseen_items(db, ctx, profile.program_id) if profile and profile.program_id else []
    skills = await rows(db, TTSkill, TTSkill.org_id == ctx.org_id, TTSkill.program_id == profile.program_id) if profile and profile.program_id else []
    masters = {m.skill_id: m for m in await rows(db, TTMastery, TTMastery.org_id == ctx.org_id, TTMastery.user_id == ctx.user.id)}
    lessons = await rows(db, TTLesson, TTLesson.org_id == ctx.org_id, TTLesson.program_id == profile.program_id,
                         TTLesson.status == 'published', TTLesson.is_sample == False) if profile and profile.program_id else []
    completed_lessons = {p.lesson_id for p in await rows(db, TTLessonProgress, TTLessonProgress.org_id == ctx.org_id,
                                                        TTLessonProgress.user_id == ctx.user.id)}
    due = sorted(await rows(db, TTReview, TTReview.org_id == ctx.org_id, TTReview.user_id == ctx.user.id,
                            TTReview.status == 'pending', TTReview.due_at <= utcnow()), key=lambda r: as_utc(r.due_at))
    used, missing, planned_lessons = set(), set(), set()
    from src.services.thinktogether.learning_support import load_plan_support, support_for_skill, supported_minutes
    initial_support, performance_support = await load_plan_support(db, ctx, profile)
    initial_minutes_used = 0
    applied_support = {}
    def add(item, kind, reason, review=None):
        nonlocal initial_minutes_used
        if item.family_id in used or plan.total_minutes+item.estimated_minutes > budget:
            return False
        support = support_for_skill(initial_support, performance_support, item.skill_id)
        if kind != 'review' and not supported_minutes(item.estimated_minutes, budget-plan.total_minutes, support, initial_minutes_used):
            return False
        plan.activities = [*plan.activities, {'kind': kind, 'item_id': item.public_id,
            'skill_id': next((s.public_id for s in skills if s.id == item.skill_id), None),
            'minutes': item.estimated_minutes, 'reason': reason,
            'review_id': review.public_id if review else None, 'support': support}]
        plan.total_minutes += item.estimated_minutes
        if kind != 'review' and support.get('source') == 'initial_assessment':
            initial_minutes_used += item.estimated_minutes
        applied_support[support['source']] = support
        used.add(item.family_id)
        return True
    for review in due:
        candidates = [i for i in items if i.skill_id == review.skill_id and i.family_id not in used]
        if not candidates:
            missing.add(review.skill_id)
        else:
            add(candidates[0], 'review', f'Due {review.interval_days}-day retrieval review', review)
    deadline_priority = {identifier: index for index, identifier in enumerate(deadline['skill_priority'])}
    ordered = sorted(skills, key=lambda s: (deadline_priority.get(s.public_id, len(skills)), s.position, s.id))
    for skill in ordered:
        if masters.get(skill.id) and masters[skill.id].state == 'retained':
            continue
        gaps = [p for p in skill.prerequisites if p not in masters or masters[p].state not in {'independent', 'retained'}]
        target = gaps[0] if gaps else skill.id
        theory = next((l for l in sorted(lessons, key=lambda l: (l.position, l.id)) if l.skill_id == target
                       and l.id not in completed_lessons and l.id not in planned_lessons), None)
        support = support_for_skill(initial_support, performance_support, target)
        theory_minutes = supported_minutes(theory.estimated_minutes, budget-plan.total_minutes, support, initial_minutes_used,
                                            divisible=support.get('source') == 'initial_assessment') if theory else 0
        if theory and (target not in masters or masters[target].independent_days == 0) and theory_minutes:
            plan.activities = [*plan.activities, {'kind': 'lesson', 'lesson_id': theory.public_id,
                'title': theory.title, 'minutes': theory_minutes, 'support': support,
                'worked_example': support.get('recommendations', {}).get('worked_example', False),
                'partial_lesson': theory_minutes < theory.estimated_minutes,
                'reason': 'Study the prerequisite concept before independent application'}]
            plan.total_minutes += theory_minutes
            if support.get('source') == 'initial_assessment':
                initial_minutes_used += theory_minutes
            applied_support[support['source']] = support
            planned_lessons.add(theory.id)
            used.update(theory.case_families)
        candidates = sorted([i for i in items if i.skill_id == target and i.family_id not in used], key=lambda i: (i.difficulty, i.id))
        if not candidates:
            missing.add(target)
        else:
            add(candidates[0], 'practice', 'Close a prerequisite gap' if gaps else 'Build independent evidence on an unseen case')
    plan.rationale = ['Due retrieval reviews come first.', 'Prerequisite gaps precede dependent skills.',
                      'Only unseen case families can support independent mastery.']
    for source, support in applied_support.items():
        prefix = f"Initial expert support policy {support['policy_version']}: " if source == 'initial_assessment' else ''
        plan.rationale.append(prefix + support['explanation'])
    if deadline['status'] != 'not_configured':
        from src.services.thinktogether.learning_deadline import deadline_rationale
        plan.rationale.append(deadline_rationale(deadline))
        plan.rationale.extend(warning['message'] for warning in deadline['warnings'])
    if deadline['status'] in {'exam_today', 'exam_passed'}:
        plan.activities, plan.total_minutes, plan.status = [], 0, 'blocked'
    if missing or not items:
        case = await shortage(db, ctx, profile.program_id if profile else None, 'No suitable approved unseen cases', list(missing))
        plan.support_case_id = case.public_id
        plan.status = 'partial' if plan.activities else 'blocked'
    db.add(plan)
    await db.commit()
    return dict(public(plan), deadline=deadline)


async def today(db, ctx):
    profile = await profile_row(db, ctx.org_id, ctx.user.id)
    day = utcnow().astimezone(ZoneInfo(profile.timezone if profile else 'UTC')).date().isoformat()
    plans = await rows(db, TTPlan, TTPlan.org_id == ctx.org_id, TTPlan.user_id == ctx.user.id, TTPlan.plan_date == day)
    from src.services.thinktogether.learning_deadline import deadline_schedule
    selected = max(plans, key=lambda p: p.version) if plans else None
    profile_changed = bool(profile and selected and as_utc(profile.updated_at) > as_utc(selected.created_at))
    budget = profile.minutes_per_day if profile_changed else selected.budget_minutes if selected else None
    deadline = await deadline_schedule(db, ctx, profile, today_budget=budget)
    progress_changed = bool(selected and deadline.get('progress_updated_at') and
        as_utc(datetime.fromisoformat(deadline['progress_updated_at'])) > as_utc(selected.created_at))
    missing_pacing = bool(selected and profile and profile.program_id and profile.exam_date and not any(reason.startswith('Exam pacing (') for reason in selected.rationale))
    if selected is None or profile_changed or progress_changed or missing_pacing:
        plan = await build_plan(db, ctx, budget)
    else:
        plan = dict(public(selected), deadline=deadline)
    if deadline['status'] != 'not_configured':
        from src.services.thinktogether.learning_deadline import deadline_rationale
        # Refresh displayed pacing facts without rewriting historical plans.
        plan['rationale'] = [deadline_rationale(deadline) if reason.startswith('Exam pacing (') else reason
                             for reason in plan['rationale']]
    return {'profile': await get_profile(db, ctx), 'plan': plan, 'skills': await skill_map(db, ctx),
            'launch_readiness': await launch_readiness(db, ctx.org_id)}


async def practice_next(db, ctx, skill_id=None, review_id=None, mode='case'):
    from src.services.thinktogether.learning_mechanics import PRACTICE_MODES, practice_mode, followup_target
    if mode not in PRACTICE_MODES or (review_id and mode != 'case'):
        raise HTTPException(422, 'Choose case, retell or think_first; retrieval reviews use independent cases')
    readiness = await learning_readiness(db, ctx.org_id, ctx.user.id)
    if not readiness['ready'] and not ctx.can('content'):
        raise HTTPException(409, {'message': 'Complete the required diagnostics before independent practice', 'readiness': readiness})
    profile = await profile_row(db, ctx.org_id, ctx.user.id)
    if not profile or not profile.program_id:
        raise HTTPException(409, 'Choose a program in your learning profile')
    items = await unseen_items(db, ctx, profile.program_id, include_coached=True)
    if ctx.can('content') and not items:
        items = await rows(db, TTItem, TTItem.org_id == ctx.org_id, TTItem.program_id == profile.program_id)
        used_families = {a.family_id for a in await rows(db, TTAttempt, TTAttempt.org_id == ctx.org_id, TTAttempt.user_id == ctx.user.id)}
        used_families.update(e.family_id for e in await rows(db, TTExposure, TTExposure.org_id == ctx.org_id, TTExposure.user_id == ctx.user.id))
        items = [i for i in items if i.is_sample and i.family_id not in used_families]
    items = [i for i in items if practice_mode(i) == mode]
    skill = await find(db, TTSkill, ctx.org_id, skill_id) if skill_id else None
    review = await find(db, TTReview, ctx.org_id, review_id, lock=True) if review_id else None
    if review and (review.user_id != ctx.user.id or review.status != 'pending' or as_utc(review.due_at) > utcnow()):
        raise HTTPException(404, 'Due review not found')
    target_id = review.skill_id if review else skill.id if skill else None
    selection_reason = 'explicit_skill' if skill else 'due_review' if review else 'plan_selection'
    if target_id is None and mode == 'case':
        target_id, selection_reason = await followup_target(db, ctx.org_id, ctx.user.id, profile.program_id)
    if target_id:
        items = [i for i in items if i.skill_id == target_id]
    if not items:
        case = await shortage(db, ctx, profile.program_id, f'No unseen expert-authored {mode} case family available', [target_id] if target_id else [])
        await db.commit()
        raise HTTPException(409, {'message': f'A content expert must add fresh {mode.replace("_", " ")} material for this skill', 'support_case_id': case.public_id})
    selected = sorted(items, key=lambda i: (i.difficulty, i.id))[0]
    if mode != 'case':
        # Reading source material or a worked AI example exposes this family
        # even if the learner leaves before submitting the coached response.
        await record_exposure(db, ctx.org_id, ctx.user.id, selected.family_id, 'coached', selected.public_id)
    result = await issue_item(db, ctx, selected,
                              phase='coached' if mode != 'case' else 'preview' if selected.is_sample else 'review' if review else 'independent', review_id=review.id if review else None)
    result['selection_reason'] = selection_reason
    await db.commit()
    return result


async def list_reviews(db, ctx):
    reviews = await rows(db, TTReview, TTReview.org_id == ctx.org_id, TTReview.user_id == ctx.user.id)
    return [public(r) for r in sorted(reviews, key=lambda r: as_utc(r.due_at))]
