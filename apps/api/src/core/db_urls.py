"""Translate PostgreSQL URLs between the app's asyncpg and migration drivers.

SQLAlchemy forwards asyncpg query values as connection keyword arguments, so
libpq's ``sslmode`` must become asyncpg's ``ssl``. Keep the requested mode as a
string: replacing verify-full with a boolean can change verification behavior.
Never log a configured URL or include it in validation errors.

https://magicstack.github.io/asyncpg/current/api/index.html#connection
"""
from sqlalchemy.engine import make_url


_SSL_MODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})


def _postgres_url(value):
    try:
        parsed = make_url(value)
    except Exception:
        raise ValueError("Database connection URL is invalid") from None
    if parsed.get_backend_name() not in {"postgres", "postgresql"}:
        return None
    return parsed


def _mode(value):
    if not isinstance(value, str) or value.lower() not in _SSL_MODES:
        raise ValueError("Database SSL mode is invalid or specified more than once")
    return value.lower()


def _translate_ssl(parsed, source, destination):
    query = dict(parsed.query)
    requested = _mode(query[source]) if source in query else None
    current = _mode(query[destination]) if destination in query else None
    if requested is not None and current is not None and requested != current:
        raise ValueError("Database SSL options conflict")
    if requested is not None:
        del query[source]
        query[destination] = requested
    elif current is not None:
        query[destination] = current
    return parsed.set(query=query)


def async_database_url(value: str) -> str:
    """Use asyncpg with equivalent SSL policy and unchanged other URL values."""
    parsed = _postgres_url(value)
    if parsed is None:
        return value
    if "channel_binding" in parsed.query:
        # asyncpg does not expose libpq's enforceable channel-binding policy.
        # Dropping a required policy would silently weaken authentication.
        raise ValueError("asyncpg does not support the channel_binding connection option")
    parsed = _translate_ssl(parsed, "sslmode", "ssl")
    return parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def sync_database_url(value: str) -> str:
    """Use the default psycopg2 dialect and libpq's equivalent SSL option."""
    parsed = _postgres_url(value)
    if parsed is None:
        return value
    parsed = _translate_ssl(parsed, "ssl", "sslmode")
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)
