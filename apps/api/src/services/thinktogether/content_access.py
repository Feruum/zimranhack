"""Additional access restriction for legacy resources attached to a TT program."""
import os

from sqlmodel import select


async def resource_access_allowed(db, user, resource_uuid: str) -> bool:
    if os.getenv('TT_ENABLED', '').lower() not in {'1', 'true', 'yes'}:
        return True
    if resource_uuid.startswith('board_'):
        from src.db.thinktogether.rooms import TTRoom
        room = (await db.execute(select(TTRoom).where(TTRoom.board_uuid == resource_uuid))).scalars().first()
        if room is not None:
            from src.db.users import PublicUser
            if not isinstance(user, PublicUser):
                return False
            from fastapi import HTTPException
            from src.services.thinktogether.common import get_context
            from src.services.thinktogether.rooms import board_access
            try:
                await board_access(db, await get_context(user, db), resource_uuid)
                return True
            except HTTPException:
                return False
    from src.db.thinktogether.core import TTResourceGrant, TTStaffGrant
    from src.db.users import PublicUser
    from src.security.org_auth import is_org_admin, is_org_member
    from src.services.thinktogether.billing import has_active_access

    grant = (await db.execute(select(TTResourceGrant).where(
        TTResourceGrant.resource_uuid == resource_uuid,
    ))).scalars().first()
    if grant is None:
        return True
    if not isinstance(user, PublicUser):
        return False
    if await is_org_admin(user.id, grant.org_id, db):
        return True
    if not await is_org_member(user.id, grant.org_id, db):
        return False
    expert = (await db.execute(select(TTStaffGrant.id).where(
        TTStaffGrant.org_id == grant.org_id, TTStaffGrant.user_id == user.id,
        TTStaffGrant.role == 'expert',
    ))).first()
    if expert:
        return True
    return bool(user.email_verified and await has_active_access(db, grant.org_id, user.id, grant.program_id))


async def guard_room_board_file(db, user, parts, request=None):
    """A room board thumbnail can contain answers; it is never public branding."""
    if os.getenv('TT_ENABLED', '').lower() not in {'1', 'true', 'yes'} or len(parts) < 4 or parts[0] != 'orgs' or parts[2] != 'boards':
        return False
    from src.db.thinktogether.rooms import TTRoom
    room = (await db.execute(select(TTRoom).where(TTRoom.board_uuid == parts[3]))).scalars().first()
    if room is None:
        return False
    from fastapi import HTTPException
    from src.db.organizations import Organization
    org = await db.get(Organization, room.org_id)
    if org is None or org.org_uuid != parts[1]:
        raise HTTPException(404, 'File not found')
    if not await resource_access_allowed(db, user, parts[3]):
        raise HTTPException(403, 'Room board access required')
    return True
