"""
Centralized organization authorization helpers.

All org membership and admin checks go through this module.
Superadmin bypass is baked in: superadmins pass every check automatically.
"""

import logging
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.user_organizations import UserOrganization
from src.db.roles import Role
from src.db.users import APITokenUser
from src.security.superadmin import is_user_superadmin
from src.security.rbac.constants import ADMIN_OR_MAINTAINER_ROLE_IDS

logger = logging.getLogger(__name__)


def refresh_org_authorization_cache(db_session: AsyncSession) -> None:
    """Share authorization reads only within the current transaction.

    SSE polling reuses the session after rollback. ORM rows are then expired,
    while cached booleans and policy decisions would otherwise survive too.
    Reset them before any superadmin bypass, and recheck on the next poll.
    """
    session = getattr(db_session, 'sync_session', None)
    if not isinstance(session, Session):
        return
    transaction = session.get_transaction()
    if transaction is not None and getattr(db_session, '_org_authorization_transaction', None) is transaction:
        return
    db_session._org_authorization_transaction = transaction
    for name in ('_user_org_cache', '_superadmin_cache', '_auth_policy_cache', '_mfa_policy_cache'):
        setattr(db_session, name, {})
    # Policy lookups also pin config rows. Expire them explicitly for sessions
    # configured with expire_on_commit=False, then let the next read reload.
    for row in session.info.pop('org_config_rows', {}).values():
        state = inspect(row)
        if state.persistent and not state.modified and state.session is session:
            session.expire(row)


async def get_user_org_rows(user_id: int, org_id: int, db_session: AsyncSession) -> list[UserOrganization]:
    """Every membership row the user has in the org (normally exactly one).

    Membership, admin status and role lookups all start from these rows, and a
    single request asks several of those questions about the same user and org.
    Memoized per transaction so they share one query. Does NOT check superadmin.
    """
    refresh_org_authorization_cache(db_session)
    _cache = getattr(db_session, '_user_org_cache', None)
    if _cache is None:
        db_session._user_org_cache = {}
        _cache = db_session._user_org_cache
    key = (user_id, org_id)
    if key in _cache:
        states = [inspect(row, raiseerr=False) for row in _cache[key]]
        if all(state is None or not (
            state.expired_attributes or state.deleted or state.was_deleted or state.detached
        ) for state in states):
            return _cache[key]
        _cache.pop(key)
    statement = select(UserOrganization).where(
        UserOrganization.user_id == user_id,
        UserOrganization.org_id == org_id,
    )
    rows = list((await db_session.execute(statement.execution_options(populate_existing=True))).scalars().all())
    # The SELECT may have opened the session's first transaction.
    refresh_org_authorization_cache(db_session)
    # Only cache positive memberships. Caching a "not a member" result is
    # unsafe: a membership granted later within the same request/session
    # (e.g. add-to-org immediately followed by an authz check) would otherwise
    # keep returning the stale "not a member" answer and incorrectly deny
    # access. Positive rows are shared only until this transaction ends.
    if rows:
        db_session._user_org_cache[key] = rows
    return rows


async def get_user_org(user_id: int, org_id: int, db_session: AsyncSession) -> Optional[UserOrganization]:
    """Return the UserOrganization row, or None. Does NOT check superadmin."""
    rows = await get_user_org_rows(user_id, org_id, db_session)
    return rows[0] if rows else None


async def holds_admin_or_maintainer_role(user_id: int, org_id: int, db_session: AsyncSession) -> bool:
    """True if any of the user's membership rows in the org is admin/maintainer.
    Does NOT check superadmin."""
    rows = await get_user_org_rows(user_id, org_id, db_session)
    return any(row.role_id in ADMIN_OR_MAINTAINER_ROLE_IDS for row in rows)


async def is_org_member(user_id: int, org_id: int, db_session: AsyncSession) -> bool:
    """Check if user is a member of the org (or a superadmin)."""
    refresh_org_authorization_cache(db_session)
    if await is_user_superadmin(user_id, db_session):
        logger.debug("Superadmin bypass: user %s accessed org %s", user_id, org_id)
        return True
    return await get_user_org(user_id, org_id, db_session) is not None


async def is_org_admin(user_id: int, org_id: int, db_session: AsyncSession) -> bool:
    """Check if user is an admin/maintainer of the org (or a superadmin)."""
    refresh_org_authorization_cache(db_session)
    if await is_user_superadmin(user_id, db_session):
        logger.debug("Superadmin bypass: user %s accessed org %s", user_id, org_id)
        return True
    user_org = await get_user_org(user_id, org_id, db_session)
    return user_org is not None and user_org.role_id in ADMIN_OR_MAINTAINER_ROLE_IDS


async def get_user_org_role(user_id: int, org_id: int, db_session: AsyncSession) -> Optional[Role]:
    """Return the user's Role in the org, or None. Does NOT check superadmin."""
    user_org = await get_user_org(user_id, org_id, db_session)
    if not user_org:
        return None
    statement = select(Role).where(Role.id == user_org.role_id)
    return (await db_session.execute(statement)).scalars().first()


async def enforce_org_mfa(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    """Apply the org's per-access session policies, if it has any.

    Despite the historical name, this is the single seam through which **all**
    org-level session policies are enforced at a request gate:

    * the "require two-factor" policy (:mod:`src.services.orgs.mfa_policy`), and
    * the auth-method / session-sharing policy
      (:mod:`src.services.orgs.auth_policy`): which methods may access the org
      and whether a central/foreign session is accepted.

    Every ``require_*`` gate and every additive call site funnels through here,
    so folding both policies in keeps them enforced in lockstep with one edit.
    Kept out of :func:`is_org_member` / :func:`is_org_admin` on purpose: those are
    plain predicates used to *shape* results (search scoping, admin-only fields),
    and raising from inside them would turn a presentation decision into a 403.
    Both policies are no-ops under default config.
    """
    from src.services.orgs.mfa_policy import enforce_org_mfa_policy
    from src.services.orgs.auth_policy import enforce_org_auth_policy

    await enforce_org_mfa_policy(db_session, user_id, org_id)
    await enforce_org_auth_policy(db_session, user_id, org_id)


async def require_org_membership(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    """Raise 403 if user is not an org member and not a superadmin."""
    if not await is_org_member(user_id, org_id, db_session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )
    await enforce_org_mfa(user_id, org_id, db_session)


async def require_org_admin(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    """Raise 403 if user is not an org admin/maintainer and not a superadmin."""
    if not await is_org_admin(user_id, org_id, db_session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization administrators and maintainers can perform this action",
        )
    await enforce_org_mfa(user_id, org_id, db_session)


async def verify_org_membership(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    """Gate of the org reporting surfaces (per-student audit, analytics):
    403 unless the user belongs to the org. Superadmins pass without the org's
    session policies; members go through them.

    Prevents cross-org data access (IDOR) by ensuring the requesting user
    actually belongs to the org whose data they are querying.
    """
    refresh_org_authorization_cache(db_session)
    if await is_user_superadmin(user_id, db_session):
        return

    membership = (await db_session.execute(
        select(UserOrganization).where(
            UserOrganization.user_id == user_id,
            UserOrganization.org_id == org_id,
        )
    )).scalars().first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    await enforce_org_mfa(user_id, org_id, db_session)


async def verify_org_admin(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    """403 unless the user's role in this org grants
    ``organizations.action_update`` (admins, maintainers, custom roles with
    that right). Superadmins pass.

    Scoped to the org being accessed, not any org the user belongs to.
    """
    refresh_org_authorization_cache(db_session)
    if await is_user_superadmin(user_id, db_session):
        return

    membership = (await db_session.execute(
        select(UserOrganization).where(
            UserOrganization.user_id == user_id,
            UserOrganization.org_id == org_id,
        )
    )).scalars().first()
    if not membership:
        raise HTTPException(status_code=403, detail="Admin access required for this organization")

    role = (await db_session.execute(
        select(Role).where(Role.id == membership.role_id)
    )).scalars().first()
    if not role:
        raise HTTPException(status_code=403, detail="Admin access required for this organization")

    if role.rights:
        rights = role.rights
        org_rights = rights.get("organizations") if isinstance(rights, dict) else getattr(rights, "organizations", None)
        if org_rights:
            has_update = org_rights.get("action_update", False) if isinstance(org_rights, dict) else getattr(org_rights, "action_update", False)
            if has_update:
                await enforce_org_mfa(user_id, org_id, db_session)
                return

    raise HTTPException(status_code=403, detail="Admin access required for this organization")


async def require_org_role_permission(
    user_id: int,
    org_id: int,
    db_session: AsyncSession,
    resource: str,
    action: str,
    fallback_role_ids: frozenset = ADMIN_OR_MAINTAINER_ROLE_IDS,
) -> None:
    """
    Check that the user has a specific permission via their org role's rights dict.
    Superadmins bypass entirely.

    Args:
        resource: e.g. 'roles', 'courses'
        action: e.g. 'action_create', 'action_read'
        fallback_role_ids: allowed role IDs when rights dict is absent
    """
    refresh_org_authorization_cache(db_session)
    if await is_user_superadmin(user_id, db_session):
        logger.debug("Superadmin bypass: user %s accessed org %s", user_id, org_id)
        return

    user_org = await get_user_org(user_id, org_id, db_session)
    if not user_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    role = (await db_session.execute(select(Role).where(Role.id == user_org.role_id))).scalars().first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role in this organization could not be determined",
        )

    if role.rights and isinstance(role.rights, dict):
        resource_rights = role.rights.get(resource, {})
        if not resource_rights.get(action, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You don't have permission to perform this action ({resource}.{action})",
            )
    else:
        if role.id not in fallback_role_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin or Maintainer role required for this action",
            )

    # Applied last, once the role permission itself has passed. Running it
    # earlier would also mean issuing DB queries in the middle of the role
    # lookup, which is both wasteful and surprising.
    await enforce_org_mfa(user_id, org_id, db_session)


async def require_org_create_permission(
    current_user,
    org_id: int,
    db_session: AsyncSession,
    resource: str,
) -> None:
    """Gate creating a top-level ``resource`` (e.g. ``"courses"``) in ``org_id``.

    Complements ``check_resource_access(..., "<type>_x", CREATE)``, whose
    placeholder carries no org and so accepts a role held in any org. API
    tokens are rights-checked there already; here they are pinned to their org.
    """
    if isinstance(current_user, APITokenUser):
        if current_user.org_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API token cannot access resources outside its organization",
            )
        return
    await require_org_role_permission(
        current_user.id, org_id, db_session, resource, "action_create"
    )


async def org_owns_account(user_id: int, org_id: int, db_session: AsyncSession) -> bool:
    """Whether ``org_id`` may act on the *identity* of ``user_id``.

    A user row is global. An org that is the account's only organization is
    effectively its home and may change its email, reset its two-factor or
    scrub it; an account that also belongs to other orgs (or is a platform
    superadmin) is not any one org's to rewrite; otherwise joining a second
    org would hand that org's admins the keys to the first.
    """
    refresh_org_authorization_cache(db_session)
    if await is_user_superadmin(user_id, db_session):
        return False
    other = (await db_session.execute(
        select(UserOrganization.org_id).where(
            UserOrganization.user_id == user_id,
            UserOrganization.org_id != org_id,
        ).limit(1)
    )).scalars().first()
    return other is None


async def require_org_destroy_right(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    """Deleting an org, wiping its content or emptying its members takes
    ``organizations.delete``; admin-or-maintainer status alone is not enough."""
    await require_org_role_permission(user_id, org_id, db_session, "organizations", "action_delete")
