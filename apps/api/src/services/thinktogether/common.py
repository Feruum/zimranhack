from dataclasses import dataclass, field
import os

from fastapi import Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.thinktogether.core import TTStaffGrant
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.org_auth import require_org_membership, is_org_admin, refresh_org_authorization_cache
from src.services.thinktogether.ids import utcnow, as_utc, public_id


_PERMISSIONS = {
    'expert': frozenset({'content', 'support'}),
    'operator': frozenset({'groups', 'support'}),
    'psychometric': frozenset({'psychometrics'}),
    'billing': frozenset({'billing', 'support'}),
}


@dataclass(frozen=True)
class TTContext:
    user: PublicUser
    org_id: int
    is_staff: bool = False
    staff_roles: frozenset[str] = field(default_factory=frozenset)

    def can(self, permission: str) -> bool:
        if 'admin' in self.staff_roles:
            return True
        return any(permission in _PERMISSIONS.get(role, ()) for role in self.staff_roles)


async def get_context(
    user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db: AsyncSession = Depends(get_db_session),
) -> TTContext:
    refresh_org_authorization_cache(db)
    slug = os.getenv('TT_ORG_SLUG', os.getenv('LEARNHOUSE_INITIAL_ORG_SLUG', 'default'))
    org = (await db.execute(select(Organization).where(Organization.slug == slug))).scalars().first()
    if org is None:
        raise HTTPException(503, 'ThinkTogether organization is not configured')
    await require_org_membership(user.id, org.id, db)
    roles = {'admin'} if await is_org_admin(user.id, org.id, db) else set()
    grants = (await db.execute(select(TTStaffGrant).where(
        TTStaffGrant.org_id == org.id, TTStaffGrant.user_id == user.id,
    ))).scalars().all()
    roles.update(grant.role for grant in grants)
    return TTContext(user=user, org_id=org.id, is_staff=bool(roles), staff_roles=frozenset(roles))


def require_permission(ctx: TTContext, permission: str) -> None:
    if not ctx.can(permission):
        raise HTTPException(403, 'You do not have access to this workspace')


async def require_staff(ctx: TTContext = Depends(get_context)) -> TTContext:
    if not ctx.is_staff:
        raise HTTPException(403, 'A staff account is required')
    return ctx
