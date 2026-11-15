"""Durable job invariants use actual SQLModel transactions."""
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.fixture
async def job_db(tmp_path):
    from src.db.thinktogether.jobs import TTJob, TTSchedulerLease
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=[TTJob.__table__, TTSchedulerLease.__table__]))
    async with AsyncSession(engine, expire_on_commit=False) as db:
        yield db
    await engine.dispose()


async def test_enqueue_is_atomic_with_caller_transaction(job_db):
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.jobs import enqueue_job
    await enqueue_job(job_db, 1, "grade", {"attempt_id": 7}, "attempt:7")
    await job_db.rollback()
    assert (await job_db.exec(select(TTJob))).all() == []


async def test_enqueue_deduplicates_and_unknown_handler_retries(job_db):
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.jobs import enqueue_job, run_job
    first = await enqueue_job(job_db, 1, "unknown", {}, "unique")
    second = await enqueue_job(job_db, 1, "unknown", {}, "unique")
    assert first.id == second.id
    await job_db.commit()
    assert await run_job(job_db, first.id, {}) is False
    await job_db.refresh(first)
    assert first.status == "retry"
    assert first.attempts == 1
    assert first.last_error == "LookupError"
    assert len((await job_db.exec(select(TTJob))).all()) == 1


async def test_failed_job_dead_letters_and_recovers_expired_lease(job_db):
    from src.services.thinktogether.common import utcnow
    from src.services.thinktogether.jobs import enqueue_job, run_job, recover_leases
    job = await enqueue_job(job_db, 1, "unknown", {}, "dead")
    job.max_attempts = 1
    await job_db.commit()
    assert await run_job(job_db, job.id, {}) is False
    await job_db.refresh(job)
    assert job.status == "dead"
    job.status = "running"
    job.lease_until = utcnow() - timedelta(seconds=1)
    await job_db.commit()
    assert await recover_leases(job_db) == 1
    assert job.status == "dead"


async def test_handler_commits_effect_and_done_in_one_transaction(job_db):
    from src.services.thinktogether.jobs import enqueue_job, run_job
    job = await enqueue_job(job_db, 1, "success", {"n": 2}, "success")
    await job_db.commit()
    async def handle(db, record):
        await enqueue_job(db, record.org_id, "followup", {}, "followup")
    assert await run_job(job_db, job.id, {"success": handle}) is True
    await job_db.refresh(job)
    assert job.status == "done"


async def test_handler_failure_rolls_back_effects(job_db):
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.jobs import enqueue_job, run_job
    job = await enqueue_job(job_db, 1, "failure", {}, "failure")
    await job_db.commit()
    async def handle(db, record):
        await enqueue_job(db, record.org_id, "side-effect", {}, "rolled-back")
        raise RuntimeError("secret provider detail must not be persisted")
    assert await run_job(job_db, job.id, {"failure": handle}) is False
    assert (await job_db.exec(select(TTJob).where(TTJob.idempotency_key == "rolled-back"))).first() is None
    await job_db.refresh(job)
    assert job.last_error == "RuntimeError"


async def test_only_one_scheduler_owns_live_lease(job_db):
    from src.services.thinktogether.jobs import acquire_scheduler_lease
    assert await acquire_scheduler_lease(job_db, "scheduler-a")
    await job_db.commit()
    assert not await acquire_scheduler_lease(job_db, "scheduler-b")
    assert await acquire_scheduler_lease(job_db, "scheduler-a")


async def test_successful_deferral_commits_effects_without_spending_failure_budget(job_db):
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.jobs import JobDeferred, enqueue_job, run_job
    from src.services.thinktogether.ids import as_utc, utcnow
    job = await enqueue_job(job_db, 1, "pending-provider", {}, "pending-provider")
    await job_db.commit()
    async def handle(db, record):
        await enqueue_job(db, record.org_id, "provider-effect", {}, "saved-provider-effect")
        return JobDeferred(delay_seconds=60)
    assert await run_job(job_db, job.id, {"pending-provider": handle})
    await job_db.refresh(job)
    assert job.status == "retry" and job.attempts == 0
    assert job.completed_at is None and job.lease_token is None
    assert as_utc(job.available_at) > utcnow() + timedelta(seconds=50)
    assert (await job_db.exec(select(TTJob).where(TTJob.idempotency_key == "saved-provider-effect"))).one()
