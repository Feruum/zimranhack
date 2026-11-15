"""A logout must separate token issuance within the same wall-clock second."""
from datetime import datetime, timedelta, timezone

import pytest

from src.security import auth
from src.tests.security.test_session_revocation import _FakeRedis


@pytest.mark.parametrize('mint', [auth.create_access_token, auth.create_refresh_token])
def test_token_issuance_keeps_subsecond_precision(monkeypatch, mint):
    issued = datetime.now(timezone.utc).replace(microsecond=123456)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return issued if tz else issued.replace(tzinfo=None)

    monkeypatch.setattr(auth, 'datetime', Clock)
    token = mint({'sub': 'synthetic-logout@example.com'})
    payload = auth.jwt.decode(token, options={'verify_signature': False})
    assert payload['iat'] == pytest.approx(issued.timestamp(), abs=0.000001, rel=0)


def test_logout_revokes_old_token_but_allows_new_login_in_same_second(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(auth, '_get_revocation_redis_client', lambda: redis)
    second = datetime(2026, 11, 15, 3, 0, 0, tzinfo=timezone.utc)
    cutoff = second + timedelta(microseconds=500000)
    assert auth.revoke_user_sessions_before(41, cutoff)
    assert auth._is_token_revoked_for_user(41, second + timedelta(microseconds=100000))
    assert not auth._is_token_revoked_for_user(41, second + timedelta(microseconds=800000))


def test_logout_rejects_legacy_token_from_same_second(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(auth, '_get_revocation_redis_client', lambda: redis)
    second = datetime(2026, 11, 15, 3, 0, 0, tzinfo=timezone.utc)
    assert auth.revoke_user_sessions_before(42, second + timedelta(microseconds=500000))
    assert auth._is_token_revoked_for_user(42, second)


def test_legacy_integer_cutoff_still_applies(monkeypatch):
    redis = _FakeRedis()
    second = datetime(2026, 11, 15, 3, 0, 0, tzinfo=timezone.utc)
    redis.setex('jwt_revoked_before:43', 3600, int(second.timestamp()))
    monkeypatch.setattr(auth, '_get_revocation_redis_client', lambda: redis)
    assert auth._is_token_revoked_for_user(43, second - timedelta(seconds=1))
    assert not auth._is_token_revoked_for_user(43, second + timedelta(microseconds=1))
