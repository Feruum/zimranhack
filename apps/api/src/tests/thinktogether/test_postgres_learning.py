"""Opt-in PostgreSQL concurrency and restart gates in disposable schemas."""
import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.user_organizations import UserOrganization
from src.db.users import User
from src.db.thinktogether.jobs import TTJob
from src.db.thinktogether.learning import (
    TTAttempt, TTDelivery, TTDiagnosticRun, TTEvaluation, TTExposure,
    TTInstrument, TTItem, TTLearnerProfile, TTMastery, TTMockRun, TTProgram,
    TTReview, TTSkill,
)
from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom
from src.services.thinktogether import learning, learning_evidence, rooms
from src.services.thinktogether.jobs import run_job


@pytest.fixture
async def pg_learning():
    url = os.getenv('TT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TT_TEST_DATABASE_URL to run PostgreSQL learning concurrency gates')
    schema = 'tt_learning_test_'+uuid4().hex
    admin = create_async_engine(url)
    engines = []

    def open_engine():
        engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
        engines.append(engine)
        return engine

    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = open_engine()
    try:
        tables = {model.__table__ for model in (
            Organization, User, Role, UserOrganization, TTProgram, TTSkill, TTItem,
            TTCohort, TTCohortMember, TTRoom, TTDelivery, TTAttempt,
            TTEvaluation, TTExposure, TTMastery, TTReview, TTDiagnosticRun,
            TTInstrument, TTLearnerProfile, TTMockRun, TTJob,
        )}
        while True:
            dependencies = {fk.column.table for table in tables for fk in table.foreign_keys}-tables
            if not dependencies:
                break
            tables |= dependencies
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=list(tables)))
        async with AsyncSession(engine, expire_on_commit=False) as db:
            org = Organization(id=1, name='Isolated learning lock test',
                               slug='isolated-learning-lock-test',
                               org_uuid=str(uuid4()), email='operator@example.com')
            user = User(id=20, username='learning-lock-test', first_name='Real',
                        last_name='Learner', email='learner@example.com',
                        email_verified=True, user_uuid=str(uuid4()))
            db.add_all([org, user])
            await db.flush()
            role = Role(org_id=org.id, name='Learner',
                        role_type=RoleTypeEnum.TYPE_ORGANIZATION, role_uuid=str(uuid4()))
            db.add(role)
            await db.flush()
            db.add(UserOrganization(org_id=org.id, user_id=user.id, role_id=role.id,
                                    creation_date='', update_date=''))
            program = TTProgram(org_id=org.id, title='Reviewed curriculum',
                                status='published', is_sample=False,
                                approved_by=user.id, approval_evidence='Content review')
            db.add(program)
            await db.flush()
            skill = TTSkill(org_id=org.id, program_id=program.id,
                            code='decision', title='Decision quality')
            db.add(skill)
            await db.flush()
            item = TTItem(org_id=org.id, program_id=program.id, skill_id=skill.id,
                          family_id='one-independent-family', title='Original case',
                          prompt='Which action comes first?',
                          choices=[{'id': 'a', 'text': 'Assess'}, {'id': 'b', 'text': 'Ignore'}],
                          answer_key={'choice': 'a'}, status='published', is_sample=False,
                          approved_by=user.id, approval_evidence='Expert review')
            cohort = TTCohort(org_id=org.id, program_id=program.id,
                              title='Real discussion group', language='en', timezone='UTC',
                              schedule={}, status='active')
            db.add_all([item, cohort])
            await db.flush()
            db.add(TTCohortMember(org_id=org.id, cohort_id=cohort.id,
                                  user_id=user.id, status='active'))
            room = TTRoom(org_id=org.id, cohort_id=cohort.id, title='Discussion',
                          case_item_id=item.id, starts_at=learning.utcnow(),
                          created_by=user.id, phase='discussion')
            db.add(room)
            await db.commit()
            ctx = SimpleNamespace(org_id=org.id, user=user, is_staff=False,
                                  can=lambda permission: False)
            yield SimpleNamespace(engine=engine, open_engine=open_engine, ctx=ctx,
                                  item_id=item.public_id, item_internal_id=item.id,
                                  skill_id=skill.id, room_id=room.id)
    finally:
        for connection_engine in engines:
            await connection_engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def wait_for_advisory_lock(engine, pid, finished):
    """Observe PostgreSQL blocking rather than guessing transaction ordering."""
    async with AsyncSession(engine) as observer:
        for _ in range(100):
            waiting = (await observer.execute(text(
                "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid=:pid"
            ), {'pid': pid})).first()
            if waiting and waiting[0] == 'Lock' and waiting[1] == 'advisory':
                return
            if finished.is_set():
                pytest.fail('The concurrent operation completed without waiting for the learner transaction')
            await observer.rollback()  # each observation sees a fresh activity snapshot
            await asyncio.sleep(.02)
    pytest.fail('PostgreSQL did not report the expected advisory-lock wait')


@pytest.mark.asyncio
async def test_room_discussion_exposure_wins_race_before_independent_evidence_read(pg_learning):
    data = pg_learning
    exposed, allow_commit, submitted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    exposure_pid, attempt_pid = asyncio.Queue(), asyncio.Queue()
    tasks = []

    async def expose():
        async with AsyncSession(data.engine, expire_on_commit=False) as db:
            await exposure_pid.put((await db.execute(text('SELECT pg_backend_pid()'))).scalar_one())
            room = await db.get(TTRoom, data.room_id)
            await rooms.expose_case_to_members(db, room)
            exposed.set()
            await allow_commit.wait()
            await db.commit()

    async def submit():
        async with AsyncSession(data.engine, expire_on_commit=False) as db:
            await attempt_pid.put((await db.execute(text('SELECT pg_backend_pid()'))).scalar_one())
            result = await learning.submit_attempt(db, data.ctx, data.item_id,
                                                   {'choice': 'a'}, 'exposure-race')
            submitted.set()
            return result

    async with AsyncSession(data.engine) as blocker:
        try:
            await learning_evidence.learner_lock(blocker, data.ctx.org_id, data.ctx.user.id)
            tasks.append(asyncio.create_task(expose()))
            await wait_for_advisory_lock(data.engine, await exposure_pid.get(), exposed)
            await blocker.rollback()
            await asyncio.wait_for(exposed.wait(), 5)
            tasks.append(asyncio.create_task(submit()))
            await wait_for_advisory_lock(data.engine, await attempt_pid.get(), submitted)
            allow_commit.set()
            await asyncio.wait_for(tasks[0], 5)
            result = await asyncio.wait_for(tasks[1], 5)
        finally:
            await blocker.rollback()
            allow_commit.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert result['score'] == 1.0
    assert result['assisted'] is True and result['unseen_family'] is False
    assert result['independent_evidence'] is False
    async with AsyncSession(data.engine) as db:
        attempt = (await db.exec(select(TTAttempt))).one()
        assert attempt.assisted and not attempt.unseen_family
        mastery = (await db.exec(select(TTMastery))).one()
        assert mastery.independent_days == 0 and mastery.independent_families == 0
        assert mastery.state not in {'independent', 'retained'}
        assert (await db.exec(select(TTReview))).all() == []
        assert len((await db.exec(select(TTExposure))).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('same_key', [True, False])
async def test_three_simultaneous_submissions_persist_one_delivery_attempt(pg_learning, same_key):
    data = pg_learning
    async with AsyncSession(data.engine, expire_on_commit=False) as db:
        item = await db.get(TTItem, data.item_internal_id)
        issued = await learning.issue_item(db, data.ctx, item)
        await db.commit()
    start = asyncio.Event()

    async def submit(number):
        async with AsyncSession(data.engine, expire_on_commit=False) as db:
            await start.wait()
            try:
                return await learning.submit_attempt(
                    db, data.ctx, data.item_id, {'choice': 'a'},
                    'shared-key' if same_key else f'key-{number}',
                    delivery_id=issued['delivery_id'],
                )
            except HTTPException as denied:
                await db.rollback()
                return denied.status_code

    tasks = [asyncio.create_task(submit(number)) for number in range(3)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
    accepted = [result for result in results if isinstance(result, dict)]
    assert len({result['id'] for result in accepted}) == 1
    assert len(accepted) == (3 if same_key else 1)
    assert [result for result in results if isinstance(result, int)] == ([] if same_key else [409, 409])
    async with AsyncSession(data.engine) as db:
        assert len((await db.exec(select(TTAttempt))).all()) == 1
        assert len((await db.exec(select(TTEvaluation))).all()) == 1
        assert len((await db.exec(select(TTReview))).all()) == 4
    await data.engine.dispose()
    reopened = data.open_engine()
    async with AsyncSession(reopened) as db:
        persisted = await learning.attempt_detail(db, data.ctx, accepted[0]['id'])
        assert persisted['id'] == accepted[0]['id'] and persisted['score'] == 1.0


@pytest.mark.asyncio
async def test_pending_grade_job_survives_engine_restart_and_duplicate_delivery(pg_learning, monkeypatch):
    data = pg_learning
    monkeypatch.setenv('TT_REASONING_MODEL', 'configured-test-reasoning-model')
    async with AsyncSession(data.engine, expire_on_commit=False) as db:
        template = await db.get(TTItem, data.item_internal_id)
        item = TTItem(org_id=template.org_id, program_id=template.program_id,
                      skill_id=template.skill_id, family_id='written-reasoning',
                      title='Reasoning case', prompt='Explain your next action.',
                      kind='reasoning', rubric={'dimensions': ['causal reasoning']},
                      status='published', is_sample=False, approved_by=data.ctx.user.id,
                      approval_evidence='Expert review')
        db.add(item)
        await db.commit()
        pending = await learning.submit_attempt(db, data.ctx, item.public_id,
                                                {'text': 'Assess impact before committing.'},
                                                'durable-reasoning')
        assert pending['score'] is None and pending['status'] == 'needs_review'
        job = (await db.exec(select(TTJob).where(TTJob.kind == 'learning.grade'))).one()
        job_id = job.id
    await data.engine.dispose()
    reopened = data.open_engine()
    async with AsyncSession(reopened, expire_on_commit=False) as db:
        job = await db.get(TTJob, job_id)
        assert job.status == 'pending' and job.payload == {'attempt_id': pending['id']}
        # A legitimate expert score can settle the queued case while a provider
        # is unavailable; replay must preserve it without inventing an AI grade.
        reviewer = SimpleNamespace(org_id=data.ctx.org_id, user=data.ctx.user,
                                   can=lambda permission: permission == 'content')
        await learning.expert_evaluate(db, reviewer, pending['id'], .9,
                                       'The response identifies the decision and its causal impact.')

    async def deliver():
        async with AsyncSession(reopened, expire_on_commit=False) as db:
            return await run_job(db, job_id, {'learning.grade': learning_evidence.learning_grade_job})

    assert sorted(await asyncio.wait_for(asyncio.gather(deliver(), deliver()), 10)) == [False, True]
    await reopened.dispose()
    restarted_again = data.open_engine()
    async with AsyncSession(restarted_again, expire_on_commit=False) as db:
        assert await run_job(db, job_id, {'learning.grade': learning_evidence.learning_grade_job}) is False
        job = await db.get(TTJob, job_id)
        assert job.status == 'done' and job.attempts == 1
        detail = await learning.attempt_detail(db, data.ctx, pending['id'])
        assert detail['score'] == .9 and detail['evaluation']['source'] == 'expert'
        evaluations = (await db.exec(select(TTEvaluation).order_by(TTEvaluation.revision))).all()
        assert [row.source for row in evaluations] == ['pending', 'expert']
        assert len((await db.exec(select(TTAttempt))).all()) == 1
