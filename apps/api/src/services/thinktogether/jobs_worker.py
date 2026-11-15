"""Run `python -m src.services.thinktogether.jobs_worker worker|scheduler`.

Every Celery delivery opens and disposes an engine inside its own event loop.
No asyncpg pool or AsyncSession is reused across asyncio.run calls/forks.
"""
import argparse
import asyncio
import importlib
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from src.core.db_urls import async_database_url

from src.db.thinktogether.jobs import TTJob
from src.services.thinktogether.ids import public_id, utcnow
from src.services.thinktogether.jobs import acquire_scheduler_lease, enqueue_job, recover_leases, run_job

_celery_app = None


@asynccontextmanager
async def job_session():
    # Register models for foreign-key resolution before opening a worker session.
    from src.core.events.database import import_all_models
    import_all_models()
    url = os.getenv("TT_DATABASE_URL", "")
    if not url:
        from config.config import get_learnhouse_config
        url = str(get_learnhouse_config().database_config.sql_connection_string)
    url = async_database_url(url)
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("Production ThinkTogether jobs require PostgreSQL")
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db
    finally:
        await engine.dispose()


async def retention_job(db, job):
    from src.services.thinktogether.rooms import expire_transcripts
    await expire_transcripts(db, utcnow()-timedelta(days=30))


def handlers():
    from src.services.thinktogether.billing import process_webhook_job, refund_order_job, reconcile_payments_job
    registry = {"billing.webhook": process_webhook_job, "billing.refund": refund_order_job,
        "billing.reconcile": reconcile_payments_job, "rooms.transcript_retention": retention_job}
    from src.services.thinktogether.rooms import enforce_media_phase
    registry["room_media_phase"] = enforce_media_phase
    # App-specific handlers participate in the same transaction as job completion.
    module_names = os.getenv("TT_JOB_HANDLERS_MODULE", "src.services.thinktogether.job_handlers")
    for module_name in filter(None, (name.strip() for name in module_names.split(","))):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            if module_name != "src.services.thinktogether.job_handlers":
                raise
            continue
        registry.update(module.handlers)
    return registry


async def execute(job_id):
    async with job_session() as db:
        return await run_job(db, int(job_id), handlers())


def celery_app():
    global _celery_app
    if _celery_app is None:
        from celery import Celery
        broker = os.getenv("TT_REDIS_URL", os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"))
        if not broker.startswith(("redis://", "rediss://")):
            raise RuntimeError("ThinkTogether Celery transport must be Redis")
        app = Celery("thinktogether", broker=broker)
        app.conf.update(task_default_queue="thinktogether", task_serializer="json",
            accept_content=["json"], result_serializer="json", task_ignore_result=True,
            task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
            broker_connection_retry_on_startup=True, broker_transport_options={"visibility_timeout": 1800},
            task_soft_time_limit=840, task_time_limit=900, worker_hijack_root_logger=False)
        @app.task(name="thinktogether.execute", acks_late=True)
        def execute_task(job_id):
            return asyncio.run(execute(job_id))
        _celery_app = app
    return _celery_app


async def scheduler_tick(db, owner, send):
    """Leadership protects scheduling; SKIP LOCKED and job keys protect delivery."""
    if not await acquire_scheduler_lease(db, owner):
        await db.rollback()
        return 0
    await recover_leases(db)
    from src.db.organizations import Organization
    orgs = (await db.exec(select(Organization.id))).all()
    now = utcnow()
    for org_id in orgs:
        await enqueue_job(db, org_id, 'community.reminders', {}, f'session-reminders:{now.strftime("%Y%m%d%H%M")}')
        await enqueue_job(db, org_id, 'access.expire', {}, f'access-expiry:{now.strftime("%Y%m%d%H%M")}:{now.second//5}')
        await enqueue_job(db, org_id, "rooms.transcript_retention", {}, f"retention:{now.date().isoformat()}")
        if os.getenv("TT_POLAR_ACCESS_TOKEN"):
            await enqueue_job(db, org_id, "billing.reconcile", {}, f"reconcile:{now.strftime('%Y%m%d%H')}:{now.minute//10}")
    # Delayed checks are already durable learning rows. Materialize each due check
    # once so the app handler can update Today or send an authorized notification.
    from src.db.thinktogether.learning import TTReview
    reviews = (await db.exec(select(TTReview).where(TTReview.status == "pending", TTReview.due_at <= now))).all()
    for review in reviews:
        await enqueue_job(db, review.org_id, "learning.review_due", {"review_id": review.id}, f"review-due:{review.id}")
    await db.commit()
    due = (await db.exec(select(TTJob).where(TTJob.status.in_(["pending", "retry"]),
        TTJob.available_at <= now, (TTJob.dispatched_at == None) | (TTJob.dispatched_at < now-timedelta(seconds=60)))
        .order_by(TTJob.available_at, TTJob.id).limit(100).with_for_update(skip_locked=True))).all()
    sent = 0
    for job in due:
        # Sending a duplicate is safe. Committing before send could lose the job.
        send(job.id)
        job.dispatched_at = utcnow()
        db.add(job)
        sent += 1
    await db.commit()
    return sent


async def scheduler():
    owner = public_id()
    app = celery_app()
    while True:
        try:
            async with job_session() as db:
                await scheduler_tick(db, owner, lambda job_id: app.send_task("thinktogether.execute", args=[job_id]))
        except Exception as exc:
            # Keep the scheduler alive. The durable lease/outbox retries on next tick.
            # Avoid exception text because DSNs or provider secrets can be embedded.
            import logging
            logging.getLogger(__name__).error("ThinkTogether scheduler tick failed (%s)", type(exc).__name__)
        await asyncio.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="ThinkTogether durable Celery jobs")
    parser.add_argument("mode", choices=["worker", "scheduler"])
    args = parser.parse_args()
    if args.mode == "worker":
        celery_app().worker_main(["worker", "--loglevel=WARNING", "--queues=thinktogether",
            "--concurrency="+os.getenv("TT_WORKER_CONCURRENCY", "2")])
    else:
        asyncio.run(scheduler())


if __name__ == "__main__":
    main()
