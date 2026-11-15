"""UTC core writes and data-preserving migration on real PostgreSQL."""
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.core import TTAuditEvent, TTNotification, TTStaffGrant
from src.db.thinktogether.core import TTNotificationPreference
from src.db.thinktogether.learning import TTSupportCase
from src.db.users import User
from src.services.thinktogether.common import TTContext
from src.tests.thinktogether.test_postgres_learning import pg_learning


MODELS = (TTStaffGrant, TTAuditEvent, TTNotification)
INSTANT = datetime(2026, 11, 15, 3, 27, 12, 345678, tzinfo=timezone.utc)


@pytest.mark.parametrize('model', MODELS)
def test_core_created_at_metadata_accepts_aware_utc(model):
    assert model.__table__.c.created_at.type.timezone is True


@pytest.mark.parametrize('model', MODELS)
async def test_core_rows_round_trip_aware_utc_on_postgres(pg_learning, model):
    async with pg_learning.engine.begin() as connection:
        await connection.run_sync(lambda sync: SQLModel.metadata.create_all(
            sync, tables=[entry.__table__ for entry in MODELS]))
    values = dict(org_id=1, created_at=INSTANT)
    if model is TTStaffGrant:
        values.update(user_id=20, role='operator')
    elif model is TTAuditEvent:
        values.update(actor_user_id=20, action='utc.regression')
    else:
        values.update(user_id=20, dedup_key='utc-regression', title='UTC regression', href='/')
    async with AsyncSession(pg_learning.engine, expire_on_commit=False) as db:
        row = model(**values)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        assert row.created_at == INSTANT
        assert row.created_at.utcoffset().total_seconds() == 0


async def test_support_reply_resolution_and_notifications_persist_on_postgres(pg_learning, monkeypatch):
    from src.services.thinktogether import support
    monkeypatch.delenv('LEARNHOUSE_RESEND_API_KEY', raising=False)
    async with pg_learning.engine.begin() as connection:
        await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=[
            entry.__table__ for entry in (*MODELS, TTNotificationPreference, TTSupportCase)]))
    learner = TTContext(user=SimpleNamespace(id=20), org_id=1)
    staff = TTContext(user=SimpleNamespace(id=21), org_id=1, is_staff=True,
                      staff_roles=frozenset({'operator'}))
    async with AsyncSession(pg_learning.engine, expire_on_commit=False) as db:
        db.add(User(id=21, user_uuid='utc-support-staff', username='utc-support-staff',
                    first_name='UTC', last_name='Staff', email='utc-staff@example.com'))
        await db.commit()
        ticket = await support.create_ticket(db, learner, 'utc-support', 'learning',
                                             'A UTC support case', 'Please help')
        await support.add_message(db, staff, ticket['id'], 'utc-reply', 'We can help')
        result = await support.set_status(db, staff, ticket['id'], 'utc-resolve', 'resolved', 'Answered')
        assert result['status'] == 'resolved' and len(result['messages']) == 3
        assert len((await db.exec(select(TTAuditEvent))).all()) == 3
        assert len((await db.exec(select(TTNotification))).all()) == 2


async def test_core_utc_migration_preserves_values_indexes_and_reverses(pg_learning):
    migration_path = Path(__file__).resolve().parents[3] / 'migrations/versions/tt_20261115_03_utc_core.py'
    assert migration_path.exists(), 'The UTC core migration must be provided'
    spec = spec_from_file_location('tt_utc_core_migration', migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(sync, direction):
        with Operations.context(MigrationContext.configure(sync)):
            getattr(migration, direction)()

    async with pg_learning.engine.begin() as connection:
        # Legacy schema with real values and indexes, independent of metadata.
        for model in MODELS:
            table = model.__tablename__
            await connection.execute(text(f'CREATE TABLE {table} (id integer PRIMARY KEY, created_at timestamp without time zone NOT NULL)'))
            await connection.execute(text(f'CREATE INDEX ix_{table}_created_at ON {table} (created_at)'))
            await connection.execute(text(f'INSERT INTO {table} VALUES (1, :instant)'), {'instant': INSTANT.replace(tzinfo=None)})
        # A non-UTC session exposes accidental use of the server timezone.
        await connection.execute(text("SET LOCAL TIME ZONE 'Asia/Qyzylorda'"))
        for direction, aware in [('upgrade', True), ('downgrade', False), ('upgrade', True)]:
            await connection.run_sync(lambda sync: migrate(sync, direction))
            for model in MODELS:
                table = model.__tablename__
                value = (await connection.execute(text(f'SELECT created_at FROM {table} WHERE id=1'))).scalar_one()
                assert value == (INSTANT if aware else INSTANT.replace(tzinfo=None))
                columns, indexes = await connection.run_sync(lambda sync: (
                    inspect(sync).get_columns(table), inspect(sync).get_indexes(table)))
                assert next(column for column in columns if column['name'] == 'created_at')['type'].timezone is aware
                assert any(index['name'] == f'ix_{table}_created_at' for index in indexes)
