"""URL-only tests never connect to cloud services or use real credentials."""
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg


@pytest.mark.parametrize("scheme",["postgres", "postgresql", "postgresql+psycopg2", "postgresql+asyncpg"])
def test_async_neon_url_preserves_quoted_credentials_and_supported_query(scheme):
    from src.core.db_urls import async_database_url
    original = f"{scheme}://learner%40example:p%40ss%3A%2F%25%2Bword@ep-safe.example.neon.tech/db?sslmode=require&target_session_attrs=read-write&prepared_statement_cache_size=0"
    result = make_url(async_database_url(original))
    assert result.drivername == "postgresql+asyncpg"
    assert result.username == "learner@example" and result.password == "p@ss:/%+word"
    assert result.query == {"ssl":"require", "target_session_attrs":"read-write", "prepared_statement_cache_size":"0"}
    _, kwargs = PGDialect_asyncpg().create_connect_args(result)
    assert kwargs["ssl"] == "require" and "sslmode" not in kwargs


@pytest.mark.parametrize("mode",["require", "verify-ca", "verify-full", "disable", "prefer", "allow"])
def test_ssl_mode_survives_round_trip_without_weaker_verification(mode):
    from src.core.db_urls import async_database_url, sync_database_url
    source = f"postgresql://learner:synthetic@db.example/pmp?sslmode={mode}"
    asynchronous = async_database_url(source)
    assert make_url(asynchronous).query["ssl"] == mode
    synchronous = make_url(sync_database_url(asynchronous))
    assert synchronous.drivername == "postgresql"
    assert synchronous.query == {"sslmode":mode}


def test_postgres_without_tls_options_and_sqlite_remain_usable():
    from src.core.db_urls import async_database_url, sync_database_url
    assert async_database_url("postgres://learner:synthetic@db/pmp") == "postgresql+asyncpg://learner:synthetic@db/pmp"
    assert sync_database_url("postgresql+psycopg2://learner:synthetic@db/pmp") == "postgresql://learner:synthetic@db/pmp"
    for url in ["sqlite:///:memory:", "sqlite+aiosqlite:///file.db"]:
        assert async_database_url(url) == url and sync_database_url(url) == url


@pytest.mark.parametrize("query",["sslmode=verify-full&ssl=disable", "sslmode=require&sslmode=disable", "sslmode=unknown", "channel_binding=require"])
def test_conflicting_or_unsupported_security_settings_fail_without_exposing_url(query):
    from src.core.db_urls import async_database_url
    with pytest.raises(ValueError) as exc:
        async_database_url(f"postgres://learner:never-display-this@db/pmp?{query}")
    assert "never-display-this" not in str(exc.value)
    assert "postgres://" not in str(exc.value)


def test_sync_keeps_libpq_security_and_certificate_query_options():
    from src.core.db_urls import sync_database_url
    result = make_url(sync_database_url("postgresql+asyncpg://learner:synthetic@db/pmp?ssl=verify-full&sslrootcert=%2Fcerts%2Froot.pem&channel_binding=require"))
    assert result.query == {"sslmode":"verify-full", "sslrootcert":"/certs/root.pem", "channel_binding":"require"}


def test_migration_public_helper_delegates_to_shared_normalization():
    from src.core.migrations import sync_database_url
    result = make_url(sync_database_url("postgresql+asyncpg://learner:synthetic@db/pmp?ssl=verify-full"))
    assert result.query == {"sslmode":"verify-full"}
