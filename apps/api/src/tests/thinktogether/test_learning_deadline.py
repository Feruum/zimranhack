"""Deadline pacing must reflect real remaining work without claiming exam readiness."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row

import pytest

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def project(*, days=42, minutes=45, skills=None, masters=None, lessons=None, items=None, reviews=None, forms=None, runs=None, completed=None, evidence=None, now=NOW, tz='UTC', budget=None):
    from src.services.thinktogether.learning_deadline import project_deadline
    return project_deadline(
        profile=Row(exam_date=(now.date()+timedelta(days=days)).isoformat(), timezone=tz, minutes_per_day=minutes),
        skills=skills or [Row(id=1, public_id='skill-1', title='Stakeholders', position=0, prerequisites=[], estimated_minutes=10)],
        masters=masters or {}, lessons=lessons or [], completed_lessons=completed or set(),
        items=items if items is not None else [Row(skill_id=1, family_id=f'fresh-{i}', estimated_minutes=10) for i in range(4)],
        reviews=reviews or [], forms=forms or [], runs=runs or [], evidence=evidence or [], now=now, today_budget=budget,
    )


def test_six_week_route_counts_local_study_days_and_weekly_capacity():
    result = project()
    assert result['days_remaining'] == 42
    assert result['available_minutes'] == 42*45
    assert len(result['weeks']) == 6
    assert sum(week['available_minutes'] for week in result['weeks']) == 42*45
    assert result['required_minutes'] == 30  # two fresh applications + delayed check
    assert result['status'] == 'on_pace'
    assert result['weeks'][0]['focus_skills'][0]['id'] == 'skill-1'
    assert 'pass prediction' in result['notice']


def test_deadline_shortfall_asks_for_time_without_shortening_exam_or_mastery():
    forms = [Row(id=i, public_id=f'mock-{i}', title=f'Mock {i}', duration_minutes=240) for i in (1, 2)]
    result = project(days=6, minutes=30, forms=forms)
    assert result['available_minutes'] == 180
    assert result['required_minutes'] == 510
    assert result['shortfall_minutes'] == 330
    assert result['recommended_minutes_per_day'] == 85
    assert result['status'] == 'at_risk'
    assert {'time_shortfall', 'retention_window', 'long_mock_session'} <= {w['code'] for w in result['warnings']}
    assert [mock['duration_minutes'] for mock in result['mocks']] == [240, 240]


def test_completed_learning_and_mocks_reduce_remaining_load_and_do_not_mix_programs():
    forms = [Row(id=1, public_id='mock-1', title='Mock', duration_minutes=240)]
    result = project(masters={1: Row(state='retained', independent_days=3, independent_families=3, latest_score=1)},
                     lessons=[Row(id=1, skill_id=1, estimated_minutes=20)], forms=forms,
                     runs=[Row(form_id=1, status='completed', completed_at=NOW)])
    assert result['required_minutes'] == 0
    assert result['status'] == 'complete'
    assert result['mocks'] == []


def test_today_override_is_not_assumed_for_every_future_day():
    result = project(days=42, minutes=30, budget=120)
    assert result['available_minutes'] == 120+41*30


def test_timezone_and_past_exam_do_not_produce_negative_or_invented_study_time():
    # It is already Nov 16 in Tokyo even though UTC still says Nov 15.
    result = project(days=1, now=datetime(2026, 11, 15, 23, tzinfo=timezone.utc), tz='Asia/Tokyo')
    assert result['days_remaining'] == 0
    assert result['status'] == 'exam_today' and result['available_minutes'] == 0
    assert project(days=-1)['status'] == 'exam_passed'


def test_missing_novel_content_is_never_reported_as_a_feasible_route():
    result = project(items=[])
    assert result['status'] == 'needs_content'
    assert result['content_gaps'] == ['skill-1']


def test_urgent_pacing_changes_focus_without_skipping_prerequisites():
    skills = [Row(id=1, public_id='base', title='Prerequisite', position=0, prerequisites=[], estimated_minutes=5),
              Row(id=2, public_id='strong', title='Known', position=1, prerequisites=[], estimated_minutes=5),
              Row(id=3, public_id='weak', title='Weak', position=2, prerequisites=[1], estimated_minutes=5)]
    items = [Row(skill_id=s.id, family_id=f'{s.id}-{n}', estimated_minutes=5) for s in skills for n in range(3)]
    masters = {2:Row(state='developing', independent_days=0, independent_families=0, latest_score=.9),
               3:Row(state='developing', independent_days=0, independent_families=0, latest_score=.2)}
    ordinary = project(days=42, skills=skills, items=items, masters=masters)
    urgent = project(days=14, skills=skills, items=items, masters=masters)
    assert ordinary['skill_priority'] == ['base', 'strong', 'weak']
    assert urgent['skill_priority'] == ['base', 'weak', 'strong']


def test_retention_window_uses_real_seven_day_delay_from_second_success():
    master = Row(state='independent', independent_days=2, independent_families=2, latest_score=1, evidence_ids=['a','b'])
    evidence = [Row(public_id='a', created_at=NOW-timedelta(days=1)), Row(public_id='b', created_at=NOW)]
    too_soon = project(days=7, masters={1:master}, evidence=evidence)
    assert 'retention_window' in {warning['code'] for warning in too_soon['warnings']}
    enough = project(days=8, masters={1:master}, evidence=evidence)
    assert 'retention_window' not in {warning['code'] for warning in enough['warnings']}


def test_weekly_totals_account_for_retrieval_and_full_mock_sessions():
    result = project(reviews=[Row(skill_id=1, status='pending', due_at=NOW+timedelta(days=n)) for n in (1,3,7,14)],
                     forms=[Row(id=1, public_id='mock', title='Mock', duration_minutes=240)])
    assert result['required_minutes'] == 280
    assert sum(week['planned_minutes'] for week in result['weeks']) == 280
    assert sum(week['review_count'] for week in result['weeks']) == 4
    assert result['mocks'][0]['target_date'] == (NOW.date()+timedelta(days=35)).isoformat()


def test_an_existing_success_can_bring_forward_the_earliest_delayed_check():
    master = Row(state='developing', independent_days=1, independent_families=1, latest_score=1, evidence_ids=['a'])
    result = project(days=8, masters={1:master}, evidence=[Row(public_id='a', created_at=NOW-timedelta(days=1))])
    assert result['independent_applications_remaining'] == 1
    assert 'retention_window' not in {warning['code'] for warning in result['warnings']}


def test_workload_uses_distinct_family_durations_and_excludes_worked_examples():
    result = project(items=[Row(skill_id=1, family_id=f'fresh-{n}', estimated_minutes=minutes) for n,minutes in enumerate((5,10,20))])
    assert result['required_minutes'] == 35
    assert sum(week['planned_minutes'] for week in result['weeks']) == 35
    taught = project(items=[Row(skill_id=1, family_id=f'fresh-{n}', estimated_minutes=5) for n in range(3)],
                    lessons=[Row(id=1, skill_id=1, estimated_minutes=10, case_families=['fresh-0'])])
    assert taught['status'] == 'needs_content'
    assert taught['content_gaps'] == ['skill-1']


@pytest.mark.asyncio
async def test_date_and_progress_changes_rebuild_saved_today_plan(db, monkeypatch):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTProgram, TTSkill, TTItem
    ctx = Row(org_id=1, user=Row(id=101), is_staff=True, can=lambda p:True)
    program = TTProgram(org_id=1, title='Original curriculum')
    db.add(program)
    await db.flush()
    skills = [TTSkill(org_id=1, program_id=program.id, code=f's{i}', title=f'Skill {i}', position=i) for i in range(2)]
    db.add_all(skills)
    await db.flush()
    for skill in skills:
        for number in range(4):
            db.add(TTItem(org_id=1, program_id=program.id, skill_id=skill.id, family_id=f'{skill.id}-{number}',
                          title='Fresh application', prompt='Decide', choices=[{'id':'a','text':'A'}], answer_key={'choice':'a'},
                          estimated_minutes=5, status='published', is_sample=False))
    await db.commit()
    await svc.save_profile(db, ctx, {'goal':'PMP', 'program_id':program.public_id, 'exam_date':(svc.utcnow()+timedelta(days=42)).date().isoformat(), 'minutes_per_day':30})
    first = await svc.today(db, ctx)
    await svc.save_profile(db, ctx, {'exam_date':(svc.utcnow()+timedelta(days=14)).date().isoformat(), 'minutes_per_day':10})
    changed = await svc.today(db, ctx)
    assert changed['plan']['version'] > first['plan']['version']
    assert changed['plan']['budget_minutes'] == 10
    assert changed['plan']['deadline']['days_remaining'] == 14
    before = changed['plan']['deadline']['independent_applications_remaining']
    item = (await svc.rows(db, TTItem))[0]
    await svc.submit_attempt(db, ctx, item.public_id, {'choice':'a'}, 'deadline-progress')
    refreshed = await svc.today(db, ctx)
    assert refreshed['plan']['version'] > changed['plan']['version']
    assert refreshed['plan']['deadline']['independent_applications_remaining'] < before
    # A one-day manual override survives progress updates without becoming the
    # learner's permanent daily availability for all future days.
    await svc.build_plan(db, ctx, 120)
    overridden = await svc.today(db, ctx)
    assert overridden['plan']['budget_minutes'] == 120
    assert overridden['plan']['deadline']['available_minutes'] == 120+13*10


@pytest.mark.asyncio
async def test_other_learners_and_programs_cannot_change_a_personal_deadline_projection(db):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTItem, TTMastery, TTProgram, TTMockForm
    ctx = Row(org_id=1, user=Row(id=101), is_staff=True, can=lambda p:True)
    sample = await svc.seed_sample(db, ctx)
    await svc.save_profile(db, ctx, {'goal':'PMP', 'program_id':sample['program']['id'],
        'exam_date':(svc.utcnow()+timedelta(days=42)).date().isoformat()})
    before = await svc.today(db, ctx)
    item = (await svc.rows(db, TTItem))[0]
    db.add(TTMastery(org_id=1, user_id=102, skill_id=item.skill_id, state='retained', independent_days=3, independent_families=3))
    other = TTProgram(org_id=1, title='Other program')
    db.add(other)
    await db.flush()
    db.add(TTMockForm(org_id=1, program_id=other.id, title='Private unrelated mock', form_code='A', duration_minutes=999,
                       status='published', is_sample=False))
    await db.commit()
    after = await svc.today(db, ctx)
    assert after['plan']['deadline'] == before['plan']['deadline']
    assert after['plan']['version'] == before['plan']['version']


@pytest.mark.asyncio
async def test_profile_date_validation_uses_the_learners_day_not_the_server_day(db, monkeypatch):
    from src.services.thinktogether import learning as svc
    from fastapi import HTTPException
    monkeypatch.setattr(svc, 'utcnow', lambda:datetime(2026,11,15,23,tzinfo=timezone.utc))
    ctx = Row(org_id=1, user=Row(id=101), is_staff=True, can=lambda p:True)
    with pytest.raises(HTTPException) as expired:
        await svc.save_profile(db, ctx, {'timezone':'Asia/Tokyo', 'exam_date':'2026-11-15'})
    assert expired.value.status_code == 422
    profile = await svc.save_profile(db, ctx, {'timezone':'America/Los_Angeles', 'exam_date':'2026-11-15'})
    assert profile['exam_date'] == '2026-11-15'


@pytest.mark.asyncio
async def test_partial_profile_does_not_replan_on_every_read_and_applies_new_time_budget(db):
    from src.services.thinktogether import learning as svc
    ctx = Row(org_id=1, user=Row(id=101), is_staff=True, can=lambda p:True)
    profile = await svc.save_profile(db, ctx, {'goal':'Prepare for PMP',
        'exam_date':(svc.utcnow()+timedelta(days=42)).date().isoformat(), 'minutes_per_day':45, 'timezone':'UTC'})
    assert profile.get('program_id') is None
    assert profile['readiness']['profile_complete'] is False
    first = await svc.today(db, ctx)
    repeated = await svc.today(db, ctx)
    assert repeated['plan']['version'] == first['plan']['version']
    await svc.save_profile(db, ctx, {'minutes_per_day':15})
    updated = await svc.today(db, ctx)
    assert updated['plan']['version'] > first['plan']['version']
    assert updated['plan']['budget_minutes'] == 15
    assert (await svc.today(db, ctx))['plan']['version'] == updated['plan']['version']


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_incomplete_content_never_claims_a_total_workload_in_plan_reasons(db, legacy):
    from src.services.thinktogether import learning as svc
    from src.db.thinktogether.learning import TTPlan
    ctx = Row(org_id=1, user=Row(id=101), is_staff=True, can=lambda p:True)
    sample = await svc.seed_sample(db, ctx)
    await svc.save_profile(db, ctx, {'goal':'PMP', 'program_id':sample['program']['id'],
        'exam_date':(svc.utcnow()+timedelta(days=42)).date().isoformat(), 'minutes_per_day':45})
    first = await svc.today(db, ctx)
    assert first['plan']['deadline']['status'] == 'needs_content'
    stored = (await svc.rows(db, TTPlan, TTPlan.user_id == ctx.user.id))[0]
    if legacy:
        stored.rationale = ['Due retrieval reviews come first.',
            'Exam pacing (exam-pacing-v1): 42 study days remain; estimated remaining work is 45 minutes against 1890 available minutes.']
        db.add(stored)
        await db.commit()
    persisted_reasons = list(stored.rationale)
    result = await svc.today(db, ctx)
    reasons = ' '.join(result['plan']['rationale'])
    assert 'estimated remaining work is' not in reasons
    assert 'workload estimates need approved course material' in reasons
    assert '1890 available minutes' in reasons
    assert result['plan']['version'] == first['plan']['version']
    await db.refresh(stored)
    assert stored.rationale == persisted_reasons  # Preserve the historical plan.
