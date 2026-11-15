"""Transactional outbox with leases, fencing, bounded retries and dead letters."""
from datetime import timedelta
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import or_, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.jobs import TTJob, TTSchedulerLease
from src.services.thinktogether.ids import public_id, utcnow

@dataclass(frozen=True)
class JobDeferred:
    """Provider accepted the command; commit its facts and poll again later.

    This is successful processing, so waiting for settlement does not consume
    the bounded retry budget reserved for transport and processing failures.
    """
    delay_seconds: int = 60

    def __post_init__(self):
        if not 1 <= self.delay_seconds <= 86400:
            raise ValueError("Job deferral must be between one second and one day")


JobHandler = Callable[[AsyncSession, TTJob], Awaitable[None | JobDeferred]]


def _insert(db, model):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif db.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("ThinkTogether durable jobs require PostgreSQL (SQLite is supported for tests)")
    return insert(model)


async def enqueue_job(db: AsyncSession, org_id: int, kind: str, payload: dict, key: str) -> TTJob:
    """Add a durable job to the *caller's* transaction; never commit it here."""
    if not key or not kind:
        raise ValueError("Job kind and idempotency key are required")
    stmt = _insert(db, TTJob).values(org_id=org_id, kind=kind, payload=payload,
        idempotency_key=key, job_uuid=public_id(), status="pending", attempts=0,
        max_attempts=8, available_at=utcnow(), created_at=utcnow())
    await db.execute(stmt.on_conflict_do_nothing(index_elements=["org_id", "idempotency_key"]))
    return (await db.exec(select(TTJob).where(TTJob.org_id == org_id, TTJob.idempotency_key == key))).one()


async def run_job(db: AsyncSession, job_id: int, handlers: dict[str, JobHandler]) -> bool:
    """A Celery delivery can repeat. Exactly one lease may apply its DB effects."""
    now, token = utcnow(), public_id()
    claimed = await db.execute(update(TTJob).where(TTJob.id == job_id,
        TTJob.status.in_(["pending", "retry"]), TTJob.available_at <= now,
        TTJob.attempts < TTJob.max_attempts).values(status="running", lease_token=token,
        lease_until=now + timedelta(minutes=15), attempts=TTJob.attempts+1)
        .execution_options(synchronize_session=False).returning(TTJob.id))
    found = claimed.scalar_one_or_none()
    await db.commit()
    if found is None:
        return False
    try:
        job = (await db.exec(select(TTJob).where(TTJob.id == job_id, TTJob.lease_token == token,
            TTJob.status == "running").with_for_update().execution_options(populate_existing=True))).one()
        handler = handlers.get(job.kind)
        if handler is None:
            raise LookupError("No registered handler for durable job kind")
        result = await handler(db, job)
        if isinstance(result, JobDeferred):
            job.status, job.completed_at = "retry", None
            job.available_at = utcnow() + timedelta(seconds=result.delay_seconds)
            job.attempts -= 1
            job.dispatched_at = None
        else:
            job.status, job.completed_at = "done", utcnow()
        job.lease_until, job.lease_token, job.last_error = None, None, None
        db.add(job)
        await db.commit()
        return True
    except Exception as exc:
        # Exception text may include provider credentials or learner input.
        error_type = type(exc).__name__
        await db.rollback()
        job = (await db.exec(select(TTJob).where(TTJob.id == job_id,
            TTJob.lease_token == token).with_for_update())).one_or_none()
        if job is not None:
            job.status = "dead" if job.attempts >= job.max_attempts else "retry"
            job.available_at = utcnow() + timedelta(seconds=min(3600, 5 * 2**min(job.attempts, 10)))
            job.last_error, job.lease_until, job.lease_token = error_type, None, None
            db.add(job)
            await db.commit()
        return False


async def recover_leases(db: AsyncSession) -> int:
    jobs = (await db.exec(select(TTJob).where(TTJob.status == "running",
        TTJob.lease_until < utcnow()).with_for_update(skip_locked=True))).all()
    for job in jobs:
        job.status = "dead" if job.attempts >= job.max_attempts else "retry"
        job.lease_until, job.lease_token = None, None
        job.available_at, job.dispatched_at = utcnow(), None
        db.add(job)
    await db.flush()
    return len(jobs)


async def acquire_scheduler_lease(db: AsyncSession, owner: str, seconds: int = 50) -> bool:
    now = utcnow()
    stmt = _insert(db, TTSchedulerLease).values(org_id=0, name="tt-scheduler", owner=owner,
        expires_at=now+timedelta(seconds=seconds))
    result = await db.execute(stmt.on_conflict_do_update(index_elements=["name"],
        set_={"owner": owner, "expires_at": now+timedelta(seconds=seconds)},
        where=or_(TTSchedulerLease.expires_at <= now, TTSchedulerLease.owner == owner)).returning(TTSchedulerLease.id))
    return result.scalar_one_or_none() is not None
