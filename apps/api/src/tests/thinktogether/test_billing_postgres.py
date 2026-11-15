"""Opt-in real PostgreSQL locking test. Uses a disposable isolated schema."""
import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


async def test_two_buyers_cannot_reserve_the_last_cohort_seat():
    url = os.getenv("TT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TT_TEST_DATABASE_URL to run the PostgreSQL row-lock concurrency gate")
    from src.db.organizations import Organization
    from src.db.users import User
    from src.db.thinktogether.learning import TTProgram, TTLearnerProfile
    from src.db.thinktogether.rooms import TTCohort, TTCohortMember
    from src.db.thinktogether.billing import TTProductConfig, TTSeatReservation, TTEntitlement
    from src.services.thinktogether.billing import reserve_seat
    schema = "tt_billing_test_"+uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings":{"search_path":schema}})
    tables = [m.__table__ for m in [Organization,User,TTProgram,TTLearnerProfile,TTCohort,TTCohortMember,TTProductConfig,TTSeatReservation,TTEntitlement]]
    try:
        async with engine.begin() as conn:
            # user/org legacy columns have a few FKs to other metadata tables;
            # create their dependency closure, preserving actual SQLModel types.
            selected = set(tables)
            while True:
                extra = {fk.column.table for table in selected for fk in table.foreign_keys} - selected
                if not extra:
                    break
                selected |= extra
            await conn.run_sync(lambda c: SQLModel.metadata.create_all(c,tables=list(selected)))
        async with AsyncSession(engine,expire_on_commit=False) as db:
            org = Organization(id=1,name="Lock test",slug="tt-lock-test",org_uuid=str(uuid4()),email="operator@example.com")
            db.add(org)
            for uid in [10,11,12,13,20,21]:
                db.add(User(id=uid,user_uuid=str(uuid4()),username=f"buyer{uid}",first_name="Real",last_name="Learner",
                    email=f"buyer{uid}@example.com",email_verified=True))
            await db.flush()
            program = TTProgram(org_id=1,title="Lock test",is_sample=False)
            db.add(program)
            await db.flush()
            cohort = TTCohort(org_id=1,program_id=program.id,title="Four real learners",language="en",timezone="UTC",
                schedule={"weekday":1,"local_time":"18:00"},capacity=5,status="active",
                exam_window_start=date(2027,1,1),exam_window_end=date(2027,2,1))
            product = TTProductConfig(org_id=1,program_id=program.id,polar_product_id="configured-product")
            db.add_all([cohort,product])
            await db.flush()
            for uid in [10,11,12,13]:
                db.add(TTCohortMember(org_id=1,user_id=uid,cohort_id=cohort.id,status="active"))
            for uid in [20,21]:
                db.add(TTLearnerProfile(org_id=1,user_id=uid,program_id=program.id,goal="PMP",exam_date="2027-01-15",
                    language="en",timezone="UTC",schedule_slots=[{"weekday":1,"local_time":"18:00"}]))
            await db.commit()
            product_id, cohort_uuid = product.id, cohort.cohort_uuid
        async def buy(uid):
            async with AsyncSession(engine,expire_on_commit=False) as db:
                user, product = await db.get(User,uid), await db.get(TTProductConfig,product_id)
                try:
                    await reserve_seat(db,1,user,product,cohort_uuid)
                    await db.commit()
                    return "held"
                except HTTPException as exc:
                    await db.rollback()
                    return exc.status_code
        assert sorted(await asyncio.gather(buy(20),buy(21)),key=str) == [409,"held"]
        async with AsyncSession(engine) as db:
            assert len((await db.exec(select(TTSeatReservation))).all()) == 1
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
