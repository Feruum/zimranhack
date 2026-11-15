import pytest

from src.db.thinktogether.core import TTResourceGrant
from src.db.thinktogether.learning import TTProgram
from src.services.thinktogether.content_access import resource_access_allowed


@pytest.mark.asyncio
async def test_paid_mapping_rejects_an_unentitled_org_member(monkeypatch, db, org, regular_user):
    monkeypatch.setenv('TT_ENABLED', 'true')
    program = TTProgram(org_id=org.id, title='PMP', status='published')
    db.add(program)
    await db.flush()
    db.add(TTResourceGrant(org_id=org.id, program_id=program.id, resource_uuid='course_paid'))
    await db.commit()
    assert not await resource_access_allowed(db, regular_user, 'course_paid')
    assert await resource_access_allowed(db, regular_user, 'course_other')


@pytest.mark.asyncio
async def test_content_gate_does_not_change_other_deployments(monkeypatch, db, regular_user):
    monkeypatch.delenv('TT_ENABLED', raising=False)
    assert await resource_access_allowed(db, regular_user, 'course_any')
