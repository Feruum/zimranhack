"""Developer tool: render an explicit, reviewable first TT migration snapshot.

Run once with the fork API's Python environment. The generated migration has no
runtime model imports and does not reconcile or silently change legacy tables.
"""
from pathlib import Path
from alembic.autogenerate import render_python_code
from alembic.migration import MigrationContext
from alembic.operations import ops
from src.core.migrations import load_models, head_revisions

metadata = load_models()
tables = sorted((t for t in metadata.tables.values() if t.name.startswith('tt_')), key=lambda t:t.name)
heads = head_revisions()
if len(heads) != 1:
    raise RuntimeError(f'Expected one migration head, got {heads}')
revision = 'tt_20261114_01'
destination = Path('migrations/versions') / f'{revision}_thinktogether.py'
if destination.exists():
    raise RuntimeError('Refusing to replace an existing historical migration')
upgrade, downgrade, foreign = [], [], []
for table in tables:
    create = ops.CreateTableOp.from_table(table)
    create.columns = [c for c in create.columns if c.__class__.__name__ != 'ForeignKeyConstraint']
    upgrade.append(create)
    for index in sorted(table.indexes, key=lambda i:i.name):
        upgrade.append(ops.CreateIndexOp.from_index(index))
    for constraint in sorted(table.foreign_key_constraints, key=lambda c:','.join(c.column_keys)):
        fk = ops.CreateForeignKeyOp.from_constraint(constraint)
        fk.constraint_name = f"fk_{table.name}_{'_'.join(constraint.column_keys)}"
        foreign.append(fk)
upgrade.extend(foreign)
for fk in reversed(foreign):
    downgrade.append(ops.DropConstraintOp(fk.constraint_name, fk.source_table, type_='foreignkey'))
for table in reversed(tables):
    downgrade.append(ops.DropTableOp(table.name))
context = MigrationContext.configure(dialect_name='postgresql')
up = render_python_code(ops.UpgradeOps(upgrade), migration_context=context)
down = render_python_code(ops.DowngradeOps(downgrade), migration_context=context)
destination.write_text(f'''"""ThinkTogether learning, cohorts, billing and durable processing.

Explicit schema snapshot. Apply before starting a release; no model reconciliation.
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = {revision!r}
down_revision = {heads[0]!r}
branch_labels = None
depends_on = None

def upgrade():
{up}

def downgrade():
{down}
''', encoding='utf-8')
print(f'Rendered {len(tables)} tables and {len(foreign)} foreign keys: {destination}')
