import pytest
from fastapi import HTTPException

from src.services.thinktogether.common import TTContext, get_context, require_permission


def test_staff_permissions_are_separate(regular_user):
    ctx = TTContext(user=regular_user, org_id=1, is_staff=True, staff_roles=frozenset({'expert'}))
    assert ctx.can('content')
    assert not ctx.can('psychometrics')
    assert not ctx.can('billing')
    with pytest.raises(HTTPException) as error:
        require_permission(ctx, 'psychometrics')
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_context_uses_configured_org(monkeypatch, db, org, regular_user):
    monkeypatch.setenv('TT_ORG_SLUG', org.slug)
    ctx = await get_context(user=regular_user, db=db)
    assert ctx.org_id == org.id
    assert not ctx.is_staff


@pytest.mark.asyncio
async def test_context_rejects_other_organization(monkeypatch, db, other_org, regular_user):
    monkeypatch.setenv('TT_ORG_SLUG', other_org.slug)
    with pytest.raises(HTTPException) as error:
        await get_context(user=regular_user, db=db)
    assert error.value.status_code == 403
