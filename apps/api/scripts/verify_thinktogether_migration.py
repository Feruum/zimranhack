"""Round-trip the explicit migration in a disposable PostgreSQL schema."""
import uuid
import sys
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic_postgresql_enum import set_configuration
from alembic_postgresql_enum.configuration import get_configuration
from sqlalchemy import ARRAY, Column, Enum, Integer, MetaData, create_engine, text, inspect
from src.core.migrations import load_models, sync_database_url, alembic_config

def _enum_names(types):
    names = set()
    for column_type in types:
        while isinstance(column_type, ARRAY):
            column_type = column_type.item_type
        if isinstance(column_type, Enum) and column_type.native_enum and column_type.name:
            names.add(column_type.name)
    return names


def thinktogether_diffs(connection, metadata, schema):
    """Scope table and native-enum parity to the disposable TT namespace.

    The enum plugin is a schema comparator and ignores include_object(table).
    It also resolves unqualified enums using the dialect's default schema,
    rather than search_path. Set both scopes explicitly for this comparison.
    """
    inspector = inspect(connection)
    enum_names = _enum_names(column.type for table in metadata.tables.values()
                             if table.name.startswith('tt_') for column in table.columns)
    for table_name in inspector.get_table_names(schema=schema):
        if table_name.startswith('tt_'):
            enum_names.update(_enum_names(column['type'] for column in inspector.get_columns(table_name, schema=schema)))
    previous_schema = connection.dialect.default_schema_name
    previous_config = get_configuration()
    try:
        connection.dialect.default_schema_name = schema
        set_configuration(replace(previous_config, include_name=lambda name: name in enum_names))
        return compare_metadata(MigrationContext.configure(connection, opts={
            'include_object': lambda obj, name, type_, reflected, compare_to:
                name.startswith('tt_') if type_ == 'table' else True,
        }), metadata)
    finally:
        connection.dialect.default_schema_name = previous_schema
        set_configuration(previous_config)


def assert_drift_is_detected(connection, metadata, schema):
    """Negative control: metadata-only TT column drift must still be reported."""
    probe = MetaData()
    for table in metadata.tables.values():
        table.to_metadata(probe)
    table_name = sorted(table.name for table in probe.tables.values() if table.name.startswith('tt_'))[0]
    column_name = '__tt_migration_drift_probe__'
    probe.tables[table_name].append_column(Column(column_name, Integer, nullable=True))
    diffs = thinktogether_diffs(connection, probe, schema)
    assert any(isinstance(diff, tuple) and diff[0] == 'add_column' and diff[2] == table_name
               and diff[3].name == column_name for diff in diffs), 'TT column drift was not detected'


def main():
    schema = 'tt_migration_' + uuid.uuid4().hex
    metadata = load_models()
    engine = create_engine(sync_database_url())
    with engine.begin() as connection:
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            metadata.create_all(connection, tables=[table for table in metadata.tables.values()
                                                   if not table.name.startswith('tt_')], checkfirst=False)
            connection.execute(text(f'CREATE TABLE "{schema}".alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
            config = alembic_config(connection)
            revision = ScriptDirectory.from_config(config).get_revision('tt_20261114_01')
            command.stamp(config, revision.down_revision)
            command.upgrade(config, 'head')
            expected = {table.name for table in metadata.tables.values() if table.name.startswith('tt_')}
            assert expected <= set(inspect(connection).get_table_names(schema=schema))
            diffs = thinktogether_diffs(connection, metadata, schema)
            assert not diffs, str(diffs)[:1000]
            assert_drift_is_detected(connection, metadata, schema)
            command.downgrade(config, revision.down_revision)
            assert not expected & set(inspect(connection).get_table_names(schema=schema))
            command.upgrade(config, 'head')
            assert expected <= set(inspect(connection).get_table_names(schema=schema))
            print(f'PASS: upgrade, metadata parity, TT drift detection, downgrade and re-upgrade ({len(expected)} ThinkTogether tables)')
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


if __name__ == '__main__':
    main()
