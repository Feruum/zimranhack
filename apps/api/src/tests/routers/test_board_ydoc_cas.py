"""A timed-out collab PUT must not overwrite a later persisted snapshot."""

from hashlib import sha256

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.boards import Board
from src.routers.boards.boards import internal_router
from src.services.boards.boards import store_ydoc_state


def _etag(state: bytes) -> str:
    return f'"{sha256(state).hexdigest()}"'


@pytest.fixture
async def cas_board(db, org, admin_user):
    board = Board(
        board_uuid="board_87654321-4321-4321-4321-cba987654321",
        name="Ydoc CAS regression", org_id=org.id, created_by=admin_user.id,
        ydoc_state=b"initial",
    )
    db.add(board)
    await db.commit()
    return board


@pytest.fixture
async def cas_client(db, monkeypatch):
    monkeypatch.setenv("COLLAB_INTERNAL_KEY", "cas-test-internal-key")
    app = FastAPI()
    app.include_router(internal_router, prefix="/boards")
    app.dependency_overrides[get_db_session] = lambda: db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-Internal-Key": "cas-test-internal-key"},
    ) as client:
        yield client


async def test_get_ydoc_returns_content_etag_including_empty_state(cas_client, cas_board, db):
    url = f"/boards/{cas_board.board_uuid}/ydoc"
    response = await cas_client.get(url)
    assert response.content == b"initial"
    assert response.headers.get("etag") == _etag(b"initial")
    cas_board.ydoc_state = None
    await db.commit()
    empty = await cas_client.get(url)
    assert empty.content == b""
    assert empty.headers.get("etag") == _etag(b"")


async def test_late_old_put_cannot_overwrite_newer_snapshot(cas_client, cas_board):
    url = f"/boards/{cas_board.board_uuid}/ydoc"
    previous_etag = _etag(b"initial")
    newer = await cas_client.put(url, content=b"newer", headers={"If-Match": previous_etag})
    assert newer.status_code == 200
    # The older request was delayed before obtaining the board row lock.
    late_old = await cas_client.put(url, content=b"late-old", headers={"If-Match": previous_etag})
    assert late_old.status_code == 409
    assert (await cas_client.get(url)).content == b"newer"


async def test_conflict_can_retry_latest_snapshot_with_fresh_etag(cas_client, cas_board):
    url = f"/boards/{cas_board.board_uuid}/ydoc"
    old_etag = _etag(b"initial")
    assert (await cas_client.put(url, content=b"newer", headers={"If-Match": old_etag})).status_code == 200
    assert (await cas_client.put(url, content=b"latest", headers={"If-Match": old_etag})).status_code == 409
    fresh = await cas_client.get(url)
    retry = await cas_client.put(url, content=b"latest", headers={"If-Match": fresh.headers["etag"]})
    assert retry.status_code == 200
    saved = await cas_client.get(url)
    assert saved.content == b"latest"
    assert saved.headers["etag"] == _etag(b"latest")


async def test_locked_cas_refreshes_a_snapshot_already_cached_in_another_session(engine, cas_board):
    # The row lock must compare current DB bytes, not an identity-map copy
    # loaded before a competing request committed.
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as older, factory() as newer:
        cached = await older.get(Board, cas_board.id)
        assert cached.ydoc_state == b"initial"
        await store_ydoc_state(cas_board.board_uuid, b"newer", newer)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as conflict:
            await store_ydoc_state(cas_board.board_uuid, b"late-old", older, expected_etag=_etag(b"initial"))
        assert conflict.value.status_code == 409


async def test_unconditioned_existing_clients_remain_compatible(cas_client, cas_board):
    url = f"/boards/{cas_board.board_uuid}/ydoc"
    assert (await cas_client.put(url, content=b"legacy")).status_code == 200
    assert (await cas_client.get(url)).content == b"legacy"
