"""Run domain checks; local PostgreSQL integration is an explicit opt-in."""
import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if os.getenv('TT_RUN_POSTGRES_TESTS') == '1':
    from src.core.db_urls import async_database_url
    url = os.environ.get('LEARNHOUSE_SQL_CONNECTION_STRING')
    if not url:
        raise SystemExit('Local PostgreSQL test database is not configured')
    os.environ['TT_TEST_DATABASE_URL'] = async_database_url(url)

raise SystemExit(pytest.main(sys.argv[1:] or ['src/tests/thinktogether', '-q']))
