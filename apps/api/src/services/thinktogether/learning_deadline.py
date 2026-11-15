"""Explainable preparation targets, recalculated from the learner's actual work.

Durations are authored workload estimates, never an exam pass prediction. The
exam day is excluded; evidence thresholds and full mock durations stay intact.
"""
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.services.thinktogether.ids import as_utc, utcnow

VERSION = 'exam-pacing-v1'


def deadline_rationale(deadline):
    prefix = f"Exam pacing ({deadline['version']}): {deadline['days_remaining']} study days remain; "
    if deadline['status'] == 'needs_content':
        return prefix + f"workload estimates need approved course material; {deadline['available_minutes']} available minutes."
    if deadline['status'] in {'exam_today', 'exam_passed'}:
        return prefix + 'update your exam date to create a new preparation route.'
    return prefix + f"estimated remaining work is {deadline['required_minutes']} minutes against {deadline['available_minutes']} available minutes."


def _minutes(value, fallback=5):
    return max(1, int(value or fallback))


def _skill_order(skills, masters, urgent):
    by_id = {s.id: s for s in skills}
    def priority(skill):
        mastery = masters.get(skill.id)
        score = getattr(mastery, 'latest_score', None)
        return (score if score is not None else .5, skill.position, skill.id) if urgent else (skill.position, skill.id)
    ordered, visited = [], set()
    def visit(skill):
        if skill.id in visited:
            return
        visited.add(skill.id)
        for identifier in skill.prerequisites:
            parent = by_id.get(identifier)
            if parent:
                visit(parent)
        ordered.append(skill)
    for skill in sorted(skills, key=priority):
        visit(skill)
    return ordered


def project_deadline(*, profile, skills, masters, lessons, completed_lessons,
                     items, reviews, forms, runs, evidence, now=None, today_budget=None):
    now = as_utc(now or utcnow())
    zone = ZoneInfo(profile.timezone if profile else 'UTC')
    local_day = now.astimezone(zone).date()
    result = {'version': VERSION, 'status': 'not_configured', 'exam_date': getattr(profile, 'exam_date', ''),
        'days_remaining': None, 'weeks_remaining': None, 'available_minutes': 0, 'required_minutes': 0,
        'shortfall_minutes': 0, 'recommended_minutes_per_day': 0, 'independent_applications_remaining': 0,
        'pending_review_count': 0, 'content_gaps': [], 'warnings': [], 'mocks': [], 'weeks': [],
        'skill_priority': [s.public_id for s in sorted(skills, key=lambda s:(s.position, s.id))],
        'notice': 'Estimated remaining study work, not an exam pass prediction. The exam day is excluded.',
        'capacity_basis': 'Daily individual study time; group sessions are scheduled separately.'}
    if not profile or not profile.exam_date:
        return result
    exam = date.fromisoformat(profile.exam_date)
    days = max(0, (exam-local_day).days)
    result.update(days_remaining=days, weeks_remaining=math.ceil(days/7))
    if exam <= local_day:
        result['status'] = 'exam_today' if exam == local_day else 'exam_passed'
        result['warnings'] = [{'code':'exam_date', 'message':'Update your exam date to create a new preparation route.'}]
        return result
    daily = _minutes(profile.minutes_per_day)
    first_budget = _minutes(today_budget if today_budget is not None else daily)
    available = first_budget+(days-1)*daily
    exam_start = datetime.combine(exam, time.min, zone).astimezone(timezone.utc)
    future_reviews = [r for r in reviews if r.status == 'pending' and as_utc(r.due_at) < exam_start]
    reviews_by_skill = defaultdict(list)
    for review in future_reviews:
        reviews_by_skill[review.skill_id].append(review)
    candidates = defaultdict(list)
    taught_families = {family for lesson in lessons for family in getattr(lesson, 'case_families', [])}
    for item in items:
        if item.family_id not in taught_families:
            candidates[item.skill_id].append(item)
    lessons_by_skill = defaultdict(list)
    for lesson in lessons:
        if lesson.id not in completed_lessons:
            lessons_by_skill[lesson.skill_id].append(lesson)
    evidence_by_id = {a.public_id:a for a in evidence}
    workloads = []
    for skill in skills:
        mastery = masters.get(skill.id)
        state = getattr(mastery, 'state', 'new')
        independent = min(getattr(mastery, 'independent_days', 0), getattr(mastery, 'independent_families', 0))
        applications = 0 if state in {'independent', 'retained'} else max(1, 2-independent)
        retention = int(state != 'retained')
        assigned = reviews_by_skill[skill.id]
        successes = sorted((evidence_by_id[i] for i in getattr(mastery, 'evidence_ids', []) if i in evidence_by_id),
                           key=lambda a:as_utc(a.created_at))
        # A learner with a successful application on an earlier day can make
        # their second application today; otherwise another day is required.
        earlier_success = any(as_utc(a.created_at).date() < now.date() for a in successes)
        earliest = now+timedelta(days=7 if independent and earlier_success else 8)
        if state == 'independent':
            second_day = next((a for a in successes if as_utc(a.created_at).date() != as_utc(successes[0].created_at).date()), None) if successes else None
            earliest = as_utc(second_day.created_at)+timedelta(days=7) if second_day else now+timedelta(days=7)
        extra_retention = int(bool(retention and not any(as_utc(r.due_at) >= earliest for r in assigned)))
        case_count = max(applications+retention, len(assigned)+extra_retention)
        # Different applications need different families. Multiplying the
        # cheapest case's duration would undercount a mixed-duration bank.
        family_costs = {}
        for item in candidates[skill.id]:
            family_costs[item.family_id] = min(family_costs.get(item.family_id, math.inf), _minutes(item.estimated_minutes))
        case_costs = sorted(family_costs.values())[:case_count]
        if len(case_costs) < case_count:
            result['content_gaps'].append(skill.public_id)
            case_costs.extend([_minutes(skill.estimated_minutes)]*(case_count-len(case_costs)))
        lesson_minutes = sum(_minutes(l.estimated_minutes) for l in lessons_by_skill[skill.id]) if state != 'retained' else 0
        effort = lesson_minutes+sum(case_costs)
        workloads.append({'skill':skill, 'minutes':effort, 'case_costs':case_costs, 'case_count':case_count,
                          'applications':applications, 'retention':retention, 'assigned':assigned,
                          'earliest_retention':earliest, 'extra_retention':extra_retention})
        result['independent_applications_remaining'] += applications
    finished = {run.form_id for run in runs if run.status in {'completed', 'needs_review'} and getattr(run, 'completed_at', None)}
    remaining_forms = sorted((form for form in forms if form.id not in finished), key=lambda form:form.id)
    for index, form in enumerate(remaining_forms):
        target = max(local_day, exam-timedelta(days=7*(len(remaining_forms)-index)))
        result['mocks'].append({'id':form.public_id, 'title':form.title, 'duration_minutes':form.duration_minutes,
                                'target_date':target.isoformat(), 'href':'/thinktogether/program'})
    required = sum(w['minutes'] for w in workloads)+sum(f.duration_minutes for f in remaining_forms)
    result.update(available_minutes=available, required_minutes=required,
                  shortfall_minutes=max(0, required-available), recommended_minutes_per_day=math.ceil(required/days),
                  pending_review_count=len(future_reviews))
    warnings = result['warnings']
    if required > available:
        warnings.append({'code':'time_shortfall', 'message':f'The current schedule is short by approximately {required-available} study minutes. Increase your available time or move the exam date.'})
    if any(w['retention'] and w['earliest_retention'] >= exam_start for w in workloads):
        warnings.append({'code':'retention_window', 'message':'Some skills cannot receive a seven-day delayed confirmation before this exam date. The evidence requirement will not be shortened.'})
    if any(f.duration_minutes > daily for f in remaining_forms):
        warnings.append({'code':'long_mock_session', 'message':'Reserve a separate uninterrupted session for each full mock exam; its duration exceeds your usual daily study time.'})
    if result['content_gaps']:
        warnings.append({'code':'content_gap', 'message':'Your learning team needs to add fresh cases for part of this route. Available time alone does not make those tasks available.'})
    if not skills:
        warnings.append({'code':'curriculum_missing', 'message':'An approved curriculum is required to estimate the remaining study work.'})
    result['status'] = 'needs_content' if result['content_gaps'] or not skills else 'at_risk' if warnings else 'complete' if not required else 'on_pace'
    ordered = _skill_order(skills, masters, days <= 21 or required > available)
    result['skill_priority'] = [s.public_id for s in ordered]
    # A bounded preview protects the API from years-long dates. Totals still
    # account for the entire period; the next request advances this window.
    for offset in range(0, min(days, 84), 7):
        count = min(7, days-offset)
        capacity = count*daily+(first_budget-daily if offset == 0 else 0)
        result['weeks'].append({'number':offset//7+1, 'starts_on':(local_day+timedelta(days=offset)).isoformat(),
            'ends_on':(local_day+timedelta(days=offset+count-1)).isoformat(), 'available_minutes':capacity,
            'planned_minutes':0, 'focus_skills':[], 'review_count':0, 'mocks':[]})
    result['preview_truncated'] = days > 84
    def week_for(day):
        offset = max(0, (day-local_day).days)//7
        return result['weeks'][offset] if offset < len(result['weeks']) else None
    by_skill = {w['skill'].id:w for w in workloads}
    initial_total = sum(w['minutes'] for w in workloads if w['applications']) or 1
    used = 0
    for skill in ordered:
        work = by_skill[skill.id]
        if not work['minutes']:
            continue
        offset = min(max(0, days-9), math.floor(used/initial_total*max(1, days-8))) if work['applications'] else 0
        target = local_day+timedelta(days=offset)
        first_week = week_for(target)
        if first_week:
            first_week['focus_skills'].append({'id':skill.public_id, 'title':skill.title, 'target_date':target.isoformat()})
        remainder = work['minutes']
        for index, review in enumerate(work['assigned']):
            cost = work['case_costs'][index]
            week = week_for(max(local_day, as_utc(review.due_at).astimezone(zone).date()))
            if week:
                week['planned_minutes'] += cost
                week['review_count'] += 1
            remainder -= cost
        # With no existing review assignment, explicitly place the final check
        # after the minimum delay rather than claiming retention in week one.
        if work['extra_retention']:
            retention_day = max(target+timedelta(days=8) if work['applications'] else local_day,
                                work['earliest_retention'].astimezone(zone).date())
            if retention_day < exam:
                week = week_for(retention_day)
                if week:
                    cost = work['case_costs'][len(work['assigned'])]
                    week['planned_minutes'] += cost
                    week['review_count'] += 1
                    remainder -= cost
        if first_week:
            first_week['planned_minutes'] += max(0, remainder)
        used += work['minutes'] if work['applications'] else 0
    for mock in result['mocks']:
        week = week_for(date.fromisoformat(mock['target_date']))
        if week:
            week['mocks'].append(mock)
            week['planned_minutes'] += mock['duration_minutes']
    for week in result['weeks']:
        week['over_capacity'] = week['planned_minutes'] > week['available_minutes']
    if any(week['over_capacity'] for week in result['weeks']):
        warnings.append({'code':'weekly_capacity', 'message':'Some weekly targets exceed your available time. Review those weeks when adjusting your schedule.'})
        if result['status'] == 'on_pace':
            result['status'] = 'at_risk'
    return result


async def deadline_schedule(db, ctx, profile, *, now=None, today_budget=None):
    from src.db.thinktogether.learning import (TTSkill, TTMastery, TTLesson, TTLessonProgress,
        TTReview, TTMockForm, TTMockRun, TTAttempt)
    from src.services.thinktogether.learning import unseen_items
    now = as_utc(now or utcnow())
    async def rows(model, *conditions):
        return (await db.execute(select(model).where(*conditions))).scalars().all()
    if not profile or not profile.program_id:
        return project_deadline(profile=None, skills=[], masters={}, lessons=[], completed_lessons=set(),
            items=[], reviews=[], forms=[], runs=[], evidence=[], now=now)
    skills = await rows(TTSkill, TTSkill.org_id == ctx.org_id, TTSkill.program_id == profile.program_id)
    ids = [s.id for s in skills]
    masters = await rows(TTMastery, TTMastery.org_id == ctx.org_id, TTMastery.user_id == ctx.user.id, TTMastery.skill_id.in_(ids))
    lessons = await rows(TTLesson, TTLesson.org_id == ctx.org_id, TTLesson.program_id == profile.program_id,
                         TTLesson.status == 'published', TTLesson.is_sample == False)
    progress = await rows(TTLessonProgress, TTLessonProgress.org_id == ctx.org_id, TTLessonProgress.user_id == ctx.user.id,
                          TTLessonProgress.lesson_id.in_([l.id for l in lessons]))
    reviews = await rows(TTReview, TTReview.org_id == ctx.org_id, TTReview.user_id == ctx.user.id, TTReview.skill_id.in_(ids))
    forms = await rows(TTMockForm, TTMockForm.org_id == ctx.org_id, TTMockForm.program_id == profile.program_id,
                       TTMockForm.status == 'published', TTMockForm.is_sample == False)
    runs = await rows(TTMockRun, TTMockRun.org_id == ctx.org_id, TTMockRun.user_id == ctx.user.id, TTMockRun.form_id.in_([f.id for f in forms]))
    attempts = await rows(TTAttempt, TTAttempt.org_id == ctx.org_id, TTAttempt.user_id == ctx.user.id, TTAttempt.skill_id.in_(ids))
    result = project_deadline(profile=profile, skills=skills, masters={m.skill_id:m for m in masters},
        lessons=lessons, completed_lessons={p.lesson_id for p in progress}, items=await unseen_items(db, ctx, profile.program_id),
        reviews=reviews, forms=forms, runs=runs, evidence=attempts, now=now, today_budget=today_budget)
    signals = [profile.updated_at, *[m.updated_at for m in masters], *[p.completed_at for p in progress],
               *[a.created_at for a in attempts], *[r.completed_at for r in runs if r.completed_at],
               *[r.due_at for r in reviews if r.status == 'pending' and as_utc(r.due_at) <= now]]
    result['progress_updated_at'] = max(as_utc(value) for value in signals).isoformat()
    return result
