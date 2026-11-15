from datetime import timedelta
import pytest
from fastapi import HTTPException


async def test_board_snapshot_and_thumbnail_use_current_room_access(db, org, regular_user, monkeypatch):
    from src.db.users import User, AnonymousUser
    from src.db.thinktogether.learning import TTProgram
    from src.db.thinktogether.billing import TTEntitlement
    from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom
    from src.services.thinktogether.ids import utcnow
    from src.services.thinktogether.content_access import resource_access_allowed
    from src.routers.local_content import _check_content_access as local_access
    from src.routers.content_files import _check_content_access as s3_access
    monkeypatch.setenv('TT_ENABLED', 'true')
    monkeypatch.setenv('TT_ORG_SLUG', org.slug)
    account = await db.get(User, regular_user.id)
    account.email_verified = True
    db.add(account)
    user = regular_user.model_copy(update={'email_verified': True})
    program = TTProgram(org_id=org.id, title='Isolated privacy fixture')
    db.add(program)
    await db.flush()
    cohort = TTCohort(org_id=org.id, program_id=program.id, title='Privacy fixture', language='en', timezone='UTC', schedule={}, status='active')
    db.add(cohort)
    await db.flush()
    member = TTCohortMember(org_id=org.id, cohort_id=cohort.id, user_id=user.id, status='active')
    room = TTRoom(org_id=org.id, cohort_id=cohort.id, title='Privacy fixture', starts_at=utcnow(),
                  created_by=user.id, phase='discussion', board_uuid='board_room_privacy')
    grant = TTEntitlement(org_id=org.id, user_id=user.id, program_id=program.id, source='complimentary',
                          starts_at=utcnow()-timedelta(minutes=1), expires_at=utcnow()+timedelta(hours=1))
    db.add_all([member, room, grant])
    await db.commit()
    path = f'orgs/{org.org_uuid}/boards/{room.board_uuid}/thumbnails/thumbnail.jpg'
    assert await resource_access_allowed(db, user, room.board_uuid)
    assert not await resource_access_allowed(db, AnonymousUser(), room.board_uuid)
    for check in (local_access, s3_access):
        assert await check(path, user, db) is False  # private; never shared-cache branding
        with pytest.raises(HTTPException):
            await check(path, AnonymousUser(), db)
        with pytest.raises(HTTPException) as wrong_org:
            await check(path.replace(org.org_uuid, 'org_other'), user, db)
        assert wrong_org.value.status_code == 404
    room.phase = 'transfer'
    db.add(room)
    await db.commit()
    assert not await resource_access_allowed(db, user, room.board_uuid)
    for check in (local_access, s3_access):
        with pytest.raises(HTTPException) as denied:
            await check(path, user, db)
        assert denied.value.status_code == 403
    room.phase, member.status = 'summary', 'revoked'
    db.add_all([room, member])
    await db.commit()
    assert not await resource_access_allowed(db, user, room.board_uuid)
