"""Cohort membership, durable room commands and phase authorization.

Services never infer mastery from chat, boards or voice. Every interactive access
rechecks the current membership and paid entitlement; reconnects read durable state.
"""
import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select, func, update, delete

from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom, TTRoomEvent, TTRoomCommand, TTRoomConsent, TTVoiceUtterance
from src.db.users import User
from src.db.user_organizations import UserOrganization
from src.db.boards import Board, BoardMember

PHASES = ('preparation', 'individual_attempt', 'discussion', 'transfer', 'summary')
ROLES = ('facilitator', 'skeptic', 'summarizer', 'timekeeper', 'connector', 'observer')


def utcnow():
    return datetime.now(timezone.utc)


def occurrence_utc(tz: str, day: date, local_time: str, fold: int | None = None):
    try:
        zone = ZoneInfo(tz)
        parsed = time.fromisoformat(local_time)
        if parsed.tzinfo or parsed.second or parsed.microsecond:
            raise ValueError()
        naive = datetime.combine(day, parsed)
        possibilities = []
        for choice in (0, 1):
            candidate = naive.replace(tzinfo=zone, fold=choice)
            if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
                possibilities.append(candidate)
        if not possibilities:
            raise HTTPException(422, 'Local session time does not exist because of daylight saving time')
        ambiguous = len({p.utcoffset() for p in possibilities}) > 1
        if ambiguous and fold not in (0, 1):
            raise HTTPException(422, 'Ambiguous local session time requires fold 0 or 1')
        return naive.replace(tzinfo=zone, fold=fold or 0).astimezone(timezone.utc)
    except (ValueError, ZoneInfoNotFoundError):
        raise HTTPException(422, 'Use an IANA timezone and HH:MM local time')


def validate_schedule(tz: str, schedule: dict):
    try:
        weekday, duration = schedule['weekday'], schedule['duration_minutes']
        if type(weekday) is not int or weekday not in range(7) or type(duration) is not int or not 30 <= duration <= 240:
            raise ValueError()
        if schedule.get('fold') not in (None, 0, 1):
            raise ValueError()
        occurrence_utc(tz, date(2026, 1, 5), schedule['local_time'], schedule.get('fold'))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, 'Schedule requires weekday 0–6, local_time HH:MM and duration_minutes 30–240')


async def require_paid(db, org_id, user_id, program_id=None):
    from src.services.thinktogether.billing import require_active_access
    await require_active_access(db, org_id, user_id, program_id)


async def require_cohort_member(db, org_id, user_id, cohort_id):
    member = (await db.execute(select(TTCohortMember).join(TTCohort, TTCohortMember.cohort_id == TTCohort.id).join(UserOrganization, (UserOrganization.user_id == TTCohortMember.user_id) & (UserOrganization.org_id == org_id)).join(User, User.id == TTCohortMember.user_id).where(
        TTCohortMember.org_id == org_id, TTCohort.org_id == org_id, TTCohortMember.cohort_id == cohort_id,
        TTCohortMember.user_id == user_id, TTCohortMember.status == 'active', TTCohort.status != 'archived', User.email_verified == True
    ))).scalars().first()
    if member is None:
        raise HTTPException(403, 'Active cohort membership required')
    return member


async def require_room_member(db, org_id, user_id, room_id):
    room = (await db.execute(select(TTRoom).where(TTRoom.org_id == org_id, TTRoom.id == room_id))).scalar_one_or_none()
    if room is None:
        raise HTTPException(404, 'Room not found')
    await require_cohort_member(db, org_id, user_id, room.cohort_id)
    return room


async def _room(db, ctx, room_uuid, lock=False):
    stmt = select(TTRoom).where(TTRoom.org_id == ctx.org_id, TTRoom.room_uuid == room_uuid)
    if lock:
        stmt = stmt.with_for_update()
    room = (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()
    if room is None:
        raise HTTPException(404, 'Room not found')
    cohort = await db.get(TTCohort, room.cohort_id)
    if not ctx.can('groups'):
        await require_cohort_member(db, ctx.org_id, ctx.user.id, room.cohort_id)
        if not ctx.user.email_verified:
            raise HTTPException(403, 'Verified email required')
        await require_paid(db, ctx.org_id, ctx.user.id, cohort.program_id)
    return room


def serialize_room(room):
    return {'id': room.room_uuid, 'title': room.title, 'starts_at': room.starts_at.isoformat(),
            'duration_minutes': room.duration_minutes, 'phase': room.phase, 'revision': room.revision,
            'board_uuid': room.board_uuid, 'roles': room.roles, 'agent_enabled': room.agent_enabled,
            'media_enforcement_pending': room.media_enforcement_pending}


async def room_view(db, ctx, room_uuid):
    room = await _room(db, ctx, room_uuid)
    result = serialize_room(room)
    result['case'] = await shared_case(db, room) if room.phase in ('discussion', 'summary') else None
    return result


async def available_cohorts(db, org_id):
    rows = (await db.execute(select(TTCohort, func.count(TTCohortMember.id)).join(
        TTCohortMember, (TTCohortMember.cohort_id == TTCohort.id) & (TTCohortMember.org_id == org_id) & (TTCohortMember.status == 'active')
    ).where(TTCohort.org_id == org_id, TTCohort.status == 'active').group_by(TTCohort.id).having(func.count(TTCohortMember.id) >= 4))).all()
    return [{'id': c.cohort_uuid, 'title': c.title, 'language': c.language, 'timezone': c.timezone,
             'schedule': c.schedule, 'capacity': c.capacity, 'member_count': count,
             'exam_window_start': c.exam_window_start, 'exam_window_end': c.exam_window_end} for c, count in rows]


async def create_cohort(db, ctx, data):
    from src.services.thinktogether.common import require_permission
    require_permission(ctx, 'groups')
    validate_schedule(data.timezone, data.schedule)
    # Organization row is a stable lock even before the first cohort exists.
    from src.db.organizations import Organization
    await db.execute(select(Organization.id).where(Organization.id == ctx.org_id).with_for_update())
    count = (await db.execute(select(func.count()).select_from(TTCohort).where(TTCohort.org_id == ctx.org_id, TTCohort.status != 'archived'))).scalar_one()
    if count >= 4:
        raise HTTPException(409, 'Pilot supports at most four cohorts')
    if data.exam_window_start and data.exam_window_end and data.exam_window_end < data.exam_window_start:
        raise HTTPException(422, 'Exam window end must follow start')
    values = data.model_dump()
    if values.get('program_id'):
        from src.db.thinktogether.learning import TTProgram
        program = (await db.execute(select(TTProgram).where(TTProgram.org_id == ctx.org_id, TTProgram.public_id == values['program_id']))).scalar_one_or_none()
        if program is None:
            raise HTTPException(404, 'Program not found')
        values['program_id'] = program.id
    cohort = TTCohort(org_id=ctx.org_id, **values)
    db.add(cohort)
    await db.commit()
    await db.refresh(cohort)
    return cohort


async def manage_member(db, ctx, cohort_uuid, user_uuid, status):
    from src.services.thinktogether.common import require_permission
    require_permission(ctx, 'groups')
    cohort = (await db.execute(select(TTCohort).where(TTCohort.org_id == ctx.org_id, TTCohort.cohort_uuid == cohort_uuid).with_for_update())).scalar_one_or_none()
    if cohort is None or cohort.status == 'archived':
        raise HTTPException(404, 'Cohort not found')
    user = (await db.execute(select(User).join(UserOrganization, UserOrganization.user_id == User.id).where(User.user_uuid == user_uuid, UserOrganization.org_id == ctx.org_id))).scalars().first()
    if user is None or (status != 'revoked' and not user.email_verified):
        raise HTTPException(422, 'Invite an existing organization member with verified email')
    member = (await db.execute(select(TTCohortMember).where(TTCohortMember.org_id == ctx.org_id, TTCohortMember.cohort_id == cohort.id, TTCohortMember.user_id == user.id))).scalar_one_or_none()
    if status == 'active':
        from src.services.thinktogether.learning import learning_readiness
        from src.db.thinktogether.learning import TTLearnerProfile
        from src.services.thinktogether.billing import _compatible, _seat_counts
        if not (await learning_readiness(db, ctx.org_id, user.id))['ready']:
            raise HTTPException(403, 'Goals and required diagnostics must be complete')
        profile = (await db.execute(select(TTLearnerProfile).where(TTLearnerProfile.org_id == ctx.org_id, TTLearnerProfile.user_id == user.id))).scalar_one_or_none()
        if profile is None or not _compatible(cohort, profile):
            raise HTTPException(409, 'Cohort schedule, language and exam window must match the learner')
        await require_paid(db, ctx.org_id, user.id, cohort.program_id)
        seats, held = await _seat_counts(db, cohort)
        if (member is None or member.status != 'active') and seats + held >= cohort.capacity:
            raise HTTPException(409, 'Cohort is full')
    if member is None:
        member = TTCohortMember(org_id=ctx.org_id, cohort_id=cohort.id, user_id=user.id, invited_by=ctx.user.id)
    member.status = status
    db.add(member)
    await db.flush()
    # Board memberships follow revocations immediately. Collab also rechecks every mutation.
    rooms = (await db.execute(select(TTRoom).where(TTRoom.org_id == ctx.org_id, TTRoom.cohort_id == cohort.id))).scalars().all()
    for room in rooms:
        if status == 'active' and room.phase in ('discussion', 'transfer', 'summary'):
            await expose_case_to_members(db, room)
        board = (await db.execute(select(Board).where(Board.org_id == ctx.org_id, Board.board_uuid == room.board_uuid))).scalar_one_or_none() if room.board_uuid else None
        if board:
            existing = (await db.execute(select(BoardMember).where(BoardMember.board_id == board.id, BoardMember.user_id == user.id))).scalars().first()
            if status == 'active' and existing is None:
                db.add(BoardMember(board_id=board.id, user_id=user.id, role='editor', creation_date=utcnow().isoformat()))
            elif status != 'active' and existing:
                await db.delete(existing)
        if status != 'active':
            consent = (await db.execute(select(TTRoomConsent).where(TTRoomConsent.org_id == ctx.org_id, TTRoomConsent.room_id == room.id, TTRoomConsent.user_id == user.id))).scalar_one_or_none()
            if consent:
                consent.audio_processing = False
                consent.updated_at = utcnow()
                db.add(consent)
    count = (await db.execute(select(func.count()).select_from(TTCohortMember).where(TTCohortMember.org_id == ctx.org_id, TTCohortMember.cohort_id == cohort.id, TTCohortMember.status == 'active'))).scalar_one()
    if status != 'active':
        from src.services.thinktogether.room_integrations import enqueue_member_revocation
        await enqueue_member_revocation(db, ctx.org_id, cohort.id, user.id, f'membership:{member.id}:{utcnow().isoformat()}')
    cohort.status = 'active' if count >= 4 else 'forming'
    db.add(cohort)
    await db.commit()
    return {'user_id': user.user_uuid, 'status': status, 'cohort_status': cohort.status}


async def create_session(db, ctx, cohort_uuid, data):
    from src.services.thinktogether.common import require_permission
    require_permission(ctx, 'groups')
    cohort = (await db.execute(select(TTCohort).where(TTCohort.org_id == ctx.org_id, TTCohort.cohort_uuid == cohort_uuid).with_for_update())).scalar_one_or_none()
    if cohort is None:
        raise HTTPException(404, 'Cohort not found')
    members = (await db.execute(select(TTCohortMember).where(TTCohortMember.org_id == ctx.org_id, TTCohortMember.cohort_id == cohort.id, TTCohortMember.status == 'active').order_by(TTCohortMember.id))).scalars().all()
    if cohort.status != 'active' or not 4 <= len(members) <= cohort.capacity:
        raise HTTPException(409, 'A session requires four to six real active members')
    if data.local_date.weekday() != cohort.schedule['weekday']:
        raise HTTPException(422, 'Session date must match the cohort weekly schedule')
    case_item_id = None
    if getattr(data, 'case_item_id', None):
        from src.db.thinktogether.learning import TTItem
        case = (await db.execute(select(TTItem).where(TTItem.org_id == ctx.org_id, TTItem.public_id == data.case_item_id, TTItem.program_id == cohort.program_id, TTItem.status == 'published', TTItem.is_sample == False))).scalar_one_or_none()
        if case is None or not case.approved_by or not case.approval_evidence or case.rubric.get('practice_mode', 'case') != 'case':
            raise HTTPException(422, 'Choose an expert-approved case in this cohort program')
        case_item_id = case.id
    starts_at = occurrence_utc(cohort.timezone, data.local_date, cohort.schedule['local_time'], data.fold if data.fold is not None else cohort.schedule.get('fold'))
    users = []
    for member in members:
        user = await db.get(User, member.user_id)
        if not user or not user.email_verified:
            raise HTTPException(409, 'All session members need verified email')
        await require_paid(db, ctx.org_id, user.id, cohort.program_id)
        users.append(user)
    previous = (await db.execute(select(func.count()).select_from(TTRoom).where(TTRoom.org_id == ctx.org_id, TTRoom.cohort_id == cohort.id))).scalar_one()
    room = TTRoom(org_id=ctx.org_id, cohort_id=cohort.id, title=data.title, starts_at=starts_at,
                  duration_minutes=cohort.schedule['duration_minutes'], created_by=ctx.user.id, case_item_id=case_item_id,
                  roles={u.user_uuid: ROLES[(i + previous) % len(users)] for i, u in enumerate(users)})
    db.add(room)
    await db.flush()
    from uuid import uuid4
    board = Board(org_id=ctx.org_id, board_uuid='board_' + str(uuid4()), name=data.title, public=False,
                  created_by=ctx.user.id, creation_date=utcnow().isoformat(), update_date=utcnow().isoformat())
    db.add(board)
    await db.flush()
    room.board_uuid = board.board_uuid
    for user in users:
        db.add(BoardMember(board_id=board.id, user_id=user.id, role='editor', creation_date=utcnow().isoformat()))
    db.add(room)
    await db.commit()
    return serialize_room(room)


async def apply_command(db, ctx, room_uuid, command_id, expected_revision, action, payload):
    room = await _room(db, ctx, room_uuid, lock=True)
    digest = hashlib.sha256(json.dumps({'actor': ctx.user.id, 'action': action, 'payload': payload, 'expected_revision': expected_revision}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    prior = (await db.execute(select(TTRoomCommand).where(TTRoomCommand.org_id == ctx.org_id, TTRoomCommand.room_id == room.id, TTRoomCommand.command_id == command_id))).scalar_one_or_none()
    if prior:
        if prior.actor_id != ctx.user.id or prior.fingerprint != digest:
            raise HTTPException(409, 'Command identifier was already used with different content')
        return prior.response
    if room.revision != expected_revision:
        raise HTTPException(409, {'message': 'Stale room revision', 'revision': room.revision})
    changes = {}
    event_payload = {}
    if action == 'advance':
        if not ctx.can('groups'):
            raise HTTPException(403, 'Group operator permission required')
        index = PHASES.index(room.phase)
        if index == len(PHASES) - 1:
            raise HTTPException(409, 'Session is already in summary')
        changes['phase'] = PHASES[index + 1]
        changes['media_enforcement_pending'] = livekit_configured() and changes['phase'] in ('individual_attempt', 'transfer', 'summary')
        event_payload = {'phase': changes['phase']}
    elif action == 'chat':
        if room.phase in ('individual_attempt', 'transfer'):
            raise HTTPException(409, 'Discussion is closed during independent attempts')
        text = payload.get('text', '')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise HTTPException(422, 'Chat must contain 1–4000 characters')
        event_payload = {'text': text.strip(), 'author': ctx.user.user_uuid}
    elif action == 'consent_audio':
        if type(payload.get('granted')) is not bool or payload.get('consent_version') != 'audio-v1':
            raise HTTPException(422, 'Explicit audio-v1 consent decision required')
        consent = (await db.execute(select(TTRoomConsent).where(TTRoomConsent.org_id == ctx.org_id, TTRoomConsent.room_id == room.id, TTRoomConsent.user_id == ctx.user.id))).scalar_one_or_none()
        if consent is None:
            consent = TTRoomConsent(org_id=ctx.org_id, room_id=room.id, user_id=ctx.user.id)
        consent.audio_processing = payload['granted']
        consent.updated_at = utcnow()
        db.add(consent)
        event_payload = {'author': ctx.user.user_uuid, 'granted': consent.audio_processing}
    elif action == 'disable_agent':
        if not ctx.can('groups') and not ctx.can('support'):
            raise HTTPException(403, 'Moderator permission required')
        changes['agent_enabled'] = False
        event_payload = {'agent_enabled': False}
    elif action == 'approve_board_summary':
        if not ctx.can('groups'):
            raise HTTPException(403, 'Group operator permission required')
        text = payload.get('text')
        if not isinstance(text, str) or len(text) > 8000:
            raise HTTPException(422, 'Board summary must contain at most 8000 characters')
        changes['approved_board_summary'] = text
        event_payload = {'approved_board_summary': text}
    elif action == 'approve_agent_note':
        from src.services.thinktogether.board_notes import approve_proposal
        event_payload = await approve_proposal(db, room, ctx, payload)
    else:
        raise HTTPException(422, 'Unsupported room command')
    result = await db.execute(update(TTRoom).where(TTRoom.id == room.id, TTRoom.org_id == ctx.org_id, TTRoom.revision == expected_revision).values(revision=expected_revision + 1, **changes).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, 'Stale room revision')
    room.revision = expected_revision + 1
    for key, value in changes.items():
        setattr(room, key, value)
    if changes.get('phase') == 'discussion' and room.case_item_id:
        await expose_case_to_members(db, room)
    if changes.get('phase') == 'discussion' and room.agent_enabled and livekit_configured():
        from src.services.thinktogether.jobs import enqueue_job
        await enqueue_job(db, ctx.org_id, 'room_dispatch', {'room_uuid': room.room_uuid}, f'room-dispatch:{room.room_uuid}:{room.revision}')
    media_job = None
    if changes.get('phase') in ('individual_attempt', 'transfer', 'summary') and livekit_configured():
        from src.services.thinktogether.jobs import enqueue_job
        media_job = await enqueue_job(db, ctx.org_id, 'room_media_phase', {'room_uuid': room.room_uuid}, f'room-media:{room.room_uuid}:{room.revision}')
    response = serialize_room(room)
    db.add(TTRoomEvent(org_id=ctx.org_id, room_id=room.id, sequence=room.revision, kind=action, actor_id=ctx.user.id, payload=event_payload))
    db.add(TTRoomCommand(org_id=ctx.org_id, room_id=room.id, actor_id=ctx.user.id, command_id=command_id, fingerprint=digest, response=response))
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    if media_job:
        # The transaction already contains a retryable durable job. A provider
        # outage cannot discard the enforcement request after phase persistence.
        try:
            await enforce_media_phase(db, media_job)
            await db.commit()
        except Exception:
            await db.rollback()
    return response


async def board_access(db, ctx, board_uuid):
    room = (await db.execute(select(TTRoom).where(TTRoom.org_id == ctx.org_id, TTRoom.board_uuid == board_uuid))).scalar_one_or_none()
    if room is None:
        return {'managed': False, 'can_write': True}
    await _room(db, ctx, room.room_uuid)
    if room.phase in ('individual_attempt', 'transfer') and not ctx.can('groups'):
        raise HTTPException(403, 'The shared board is closed during independent work')
    return {'managed': True, 'can_write': room.phase in ('preparation', 'discussion'), 'phase': room.phase, 'revision': room.revision}


async def event_batch(db, ctx, room_uuid, after):
    room = await _room(db, ctx, room_uuid, lock=True)
    rows = (await db.execute(select(TTRoomEvent).where(TTRoomEvent.org_id == ctx.org_id, TTRoomEvent.room_id == room.id, TTRoomEvent.sequence > after).order_by(TTRoomEvent.sequence).limit(100))).scalars().all()
    private = room.phase in ('individual_attempt', 'transfer')
    hidden = {'chat', 'agent_message', 'approve_board_summary'}
    return [{'id': e.sequence, 'kind': 'withheld' if private and e.kind in hidden else e.kind,
             'payload': {} if private and e.kind in hidden else e.payload,
             'created_at': e.created_at.isoformat()} for e in rows]


async def expire_transcripts(db, cutoff):
    result = await db.execute(delete(TTVoiceUtterance).where(TTVoiceUtterance.created_at < cutoff))
    return result.rowcount


def participant_identity(room, user):
    return f'tt:{room.org_id}:{room.room_uuid}:{user.user_uuid}'


async def _consent(db, room, user_id):
    consent = (await db.execute(select(TTRoomConsent).where(TTRoomConsent.org_id == room.org_id, TTRoomConsent.room_id == room.id, TTRoomConsent.user_id == user_id))).scalar_one_or_none()
    if consent is None or not consent.audio_processing or consent.consent_version != 'audio-v1':
        raise HTTPException(403, 'Explicit consent to audio processing is required')
    return consent


async def media_token(db, ctx, room_uuid):
    # Serialize token minting with phase changes so the Cloud revocation cutoff
    # always covers tokens minted just before a private phase begins.
    room = await _room(db, ctx, room_uuid, lock=True)
    # Staff permissions never substitute for participant membership/consent.
    await require_room_member(db, ctx.org_id, ctx.user.id, room.id)
    cohort = await db.get(TTCohort, room.cohort_id)
    await require_paid(db, ctx.org_id, ctx.user.id, cohort.program_id)
    if not ctx.user.email_verified:
        raise HTTPException(403, 'Verified email required')
    url = os.getenv('TT_LIVEKIT_URL') or os.getenv('LIVEKIT_URL')
    key = os.getenv('TT_LIVEKIT_API_KEY') or os.getenv('LIVEKIT_API_KEY')
    secret = os.getenv('TT_LIVEKIT_API_SECRET') or os.getenv('LIVEKIT_API_SECRET')
    if not url or not key or not secret:
        raise HTTPException(503, 'Live video is unavailable: LiveKit is not configured')
    # WebRTC participation does not authorize transcription. The agent context
    # and utterance endpoints independently require explicit audio-v1 consent.
    if room.phase in ('individual_attempt', 'transfer', 'summary'):
        raise HTTPException(409, 'Live discussion is closed in this phase')
    import jwt
    issued = int(utcnow().timestamp())
    from src.db.thinktogether.billing import TTEntitlement
    grants = (await db.execute(select(TTEntitlement).where(TTEntitlement.org_id == ctx.org_id,
        TTEntitlement.user_id == ctx.user.id, TTEntitlement.status == 'active', TTEntitlement.starts_at <= utcnow(),
        TTEntitlement.expires_at > utcnow(), TTEntitlement.program_id == cohort.program_id))).scalars().all() if cohort.program_id else []
    expires = min(issued + 600, int(max(g.expires_at.timestamp() for g in grants))) if grants else issued + 600
    room_name = f'tt-{ctx.org_id}-{room.room_uuid}'
    token = jwt.encode({'iss': key, 'sub': participant_identity(room, ctx.user), 'name': ctx.user.first_name,
                        'iat': issued, 'nbf': issued - 5, 'exp': expires,
                        'video': {'roomJoin': True, 'room': room_name, 'canPublish': True, 'canSubscribe': True, 'canPublishData': False}}, secret, algorithm='HS256')
    if room.phase == 'discussion' and room.agent_enabled:
        from src.services.thinktogether.jobs import enqueue_job
        await enqueue_job(db, ctx.org_id, 'room_dispatch', {'room_uuid': room.room_uuid}, f'room-dispatch:{room.room_uuid}:{issued // 60}')
    await db.commit()
    return {'url': url, 'token': token, 'room': room_name, 'identity': participant_identity(room, ctx.user), 'expires_at': expires}


async def voice_context(db, room_uuid):
    room = (await db.execute(select(TTRoom).where(TTRoom.room_uuid == room_uuid).execution_options(populate_existing=True))).scalar_one_or_none()
    if room is None:
        raise HTTPException(404, 'Room not found')
    if room.phase != 'discussion' or not room.agent_enabled:
        raise HTTPException(409, 'Voice facilitator is disabled in this room phase')
    cohort = await db.get(TTCohort, room.cohort_id)
    if cohort is None or cohort.org_id != room.org_id or cohort.status == 'archived':
        raise HTTPException(403, 'Cohort unavailable')
    members = (await db.execute(select(TTCohortMember).where(TTCohortMember.org_id == room.org_id, TTCohortMember.cohort_id == cohort.id, TTCohortMember.status == 'active').order_by(TTCohortMember.id))).scalars().all()
    participants = []
    for member in members:
        user = await db.get(User, member.user_id)
        if user is None or not user.email_verified:
            continue
        try:
            await require_cohort_member(db, room.org_id, user.id, cohort.id)
            await require_paid(db, room.org_id, user.id, cohort.program_id)
            await _consent(db, room, user.id)
        except HTTPException as error:
            if error.status_code == 403:
                continue
            raise
        participants.append({'identity': participant_identity(room, user), 'user_uuid': user.user_uuid, 'role': room.roles.get(user.user_uuid)})
    # Deliberately project only session operations. No goals, cognitive results,
    # private attempts, assessment answers, emails or health data cross this boundary.
    recent = (await db.execute(select(TTRoomEvent).where(TTRoomEvent.org_id == room.org_id, TTRoomEvent.room_id == room.id, TTRoomEvent.kind.in_(['chat', 'agent_message'])).order_by(TTRoomEvent.sequence.desc()).limit(20))).scalars().all()
    return {'room_uuid': room.room_uuid, 'livekit_room': f'tt-{room.org_id}-{room.room_uuid}',
            'phase': room.phase, 'revision': room.revision, 'agent_enabled': room.agent_enabled,
            'language': cohort.language, 'participants': participants,
            'case': await shared_case(db, room), 'board_summary': room.approved_board_summary,
            'recent_chat': [{'kind': e.kind, 'text': e.payload.get('text', '')} for e in reversed(recent)],
            'facilitation': 'Ask one concise question at a time. Invite quieter participants. Never reveal answers, diagnose learners, or grade mastery. Stop when phase or consent changes.'}


async def record_utterance(db, room_uuid, utterance_id, identity, text):
    context = await voice_context(db, room_uuid)
    participant = next((p for p in context['participants'] if p['identity'] == identity), None)
    if participant is None:
        raise HTTPException(403, 'Speaker identity is not an authorized consenting room participant')
    if not text.strip() or len(text) > 8000 or not utterance_id or len(utterance_id) > 128:
        raise HTTPException(422, 'Invalid utterance')
    room = (await db.execute(select(TTRoom).where(TTRoom.room_uuid == room_uuid).with_for_update().execution_options(populate_existing=True))).scalar_one()
    if room.phase != 'discussion' or not room.agent_enabled:
        raise HTTPException(409, 'Voice facilitator is disabled in this room phase')
    user = (await db.execute(select(User).where(User.user_uuid == participant['user_uuid']))).scalar_one()
    await require_room_member(db, room.org_id, user.id, room.id)
    await _consent(db, room, user.id)
    previous = (await db.execute(select(TTVoiceUtterance).where(TTVoiceUtterance.org_id == room.org_id, TTVoiceUtterance.room_id == room.id, TTVoiceUtterance.utterance_id == utterance_id))).scalar_one_or_none()
    if previous:
        if previous.user_id != user.id or previous.text != text:
            raise HTTPException(409, 'Utterance identifier already used')
    else:
        db.add(TTVoiceUtterance(org_id=room.org_id, room_id=room.id, user_id=user.id, utterance_id=utterance_id, text=text))
        await db.commit()
    return {'accepted': True, 'utterance_id': utterance_id, 'speaker': identity}

async def shared_case(db, room):
    if not room.case_item_id:
        return None
    from src.db.thinktogether.learning import TTItem
    item = (await db.execute(select(TTItem).where(TTItem.id == room.case_item_id, TTItem.org_id == room.org_id, TTItem.status == 'published', TTItem.is_sample == False))).scalar_one_or_none()
    cohort = await db.get(TTCohort, room.cohort_id)
    if item is None or item.program_id != cohort.program_id or not item.approved_by or not item.approval_evidence or item.rubric.get('practice_mode', 'case') != 'case':
        raise HTTPException(409, 'Shared case is not approved for this program')
    from src.services.thinktogether.learning_content import safe_item
    return safe_item(item)


async def expose_case_to_members(db, room):
    if not room.case_item_id:
        return
    from src.db.thinktogether.learning import TTItem, TTExposure
    await shared_case(db, room)
    item = await db.get(TTItem, room.case_item_id)
    members = (await db.execute(select(TTCohortMember).where(TTCohortMember.org_id == room.org_id, TTCohortMember.cohort_id == room.cohort_id, TTCohortMember.status == 'active'))).scalars().all()
    from src.services.thinktogether.learning_content import record_exposure
    from src.services.thinktogether.learning_evidence import learner_lock
    for member in sorted(members, key=lambda m:m.user_id):
        await learner_lock(db, room.org_id, member.user_id)
        exists = (await db.execute(select(TTExposure.id).where(TTExposure.org_id == room.org_id, TTExposure.user_id == member.user_id, TTExposure.family_id == item.family_id, TTExposure.source == 'group', TTExposure.source_id == room.room_uuid))).scalars().first()
        if exists is None:
            await record_exposure(db, room.org_id, member.user_id, item.family_id, 'group', room.room_uuid)
    await db.flush()


async def publish_agent_message(db, room_uuid, text, message_id, facilitation_action=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000 or not message_id or len(message_id) > 128:
        raise HTTPException(422, 'Invalid facilitator message')
    room = (await db.execute(select(TTRoom).where(TTRoom.room_uuid == room_uuid).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if room is None:
        raise HTTPException(404, 'Room not found')
    if room.phase != 'discussion' or not room.agent_enabled:
        raise HTTPException(409, 'Facilitator is disabled in this room phase')
    previous = (await db.execute(select(TTRoomEvent).where(TTRoomEvent.org_id == room.org_id, TTRoomEvent.room_id == room.id, TTRoomEvent.message_id == message_id))).scalar_one_or_none()
    if previous:
        if previous.kind != 'agent_message' or previous.payload.get('text') != text or previous.payload.get('facilitation_action') != facilitation_action:
            raise HTTPException(409, 'Facilitator message identifier already used')
        return {'id': previous.sequence, 'kind': previous.kind, 'payload': previous.payload}
    revision = room.revision
    updated = await db.execute(update(TTRoom).where(TTRoom.id == room.id, TTRoom.org_id == room.org_id, TTRoom.revision == revision, TTRoom.phase == 'discussion', TTRoom.agent_enabled == True).values(revision=revision + 1).execution_options(synchronize_session=False))
    if updated.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, 'Room changed before facilitator response')
    payload = {'text': text, 'source': 'facilitator'}
    if facilitation_action is not None:
        payload['facilitation_action'] = facilitation_action
    event = TTRoomEvent(org_id=room.org_id, room_id=room.id, sequence=revision + 1, kind='agent_message', message_id=message_id, payload=payload)
    db.add(event)
    await db.commit()
    return {'id': event.sequence, 'kind': event.kind, 'payload': event.payload}


def livekit_configured():
    return bool((os.getenv('TT_LIVEKIT_URL') or os.getenv('LIVEKIT_URL')) and
                (os.getenv('TT_LIVEKIT_API_KEY') or os.getenv('LIVEKIT_API_KEY')) and
                (os.getenv('TT_LIVEKIT_API_SECRET') or os.getenv('LIVEKIT_API_SECRET')))


async def _livekit_rpc(method, payload):
    import httpx
    import jwt
    url = os.getenv('TT_LIVEKIT_URL') or os.getenv('LIVEKIT_URL', '')
    key = os.getenv('TT_LIVEKIT_API_KEY') or os.getenv('LIVEKIT_API_KEY')
    secret = os.getenv('TT_LIVEKIT_API_SECRET') or os.getenv('LIVEKIT_API_SECRET')
    if not livekit_configured():
        raise RuntimeError('LiveKit is not configured')
    url = url.replace('wss://', 'https://', 1).replace('ws://', 'http://', 1).rstrip('/')
    issued = int(utcnow().timestamp())
    token = jwt.encode({'iss': key, 'iat': issued, 'nbf': issued - 5, 'exp': issued + 60,
                        'video': {'roomAdmin': True, 'room': payload['room']}}, secret, algorithm='HS256')
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f'{url}/twirp/livekit.RoomService/{method}', json=payload,
                                     headers={'Authorization': f'Bearer {token}'})
        response.raise_for_status()
        return response.json()


async def enforce_media_phase(db, job):
    """Durable job handler; caller owns commit/retry. LiveKit Cloud is required.

    RemoveParticipant with an explicit revocation cutoff invalidates even a
    recently refreshed token on Cloud. Minted tokens last at most ten minutes,
    matching Cloud's revocation lifetime. Also revoke absent cohort identities.
    https://docs.livekit.io/frontends/reference/tokens-grants/#token-revocation
    """
    room = (await db.execute(select(TTRoom).where(TTRoom.room_uuid == job.payload['room_uuid'], TTRoom.org_id == job.org_id).execution_options(populate_existing=True))).scalar_one_or_none()
    if room is None or room.phase in ('preparation', 'discussion') or not livekit_configured():
        return
    room_name = f'tt-{room.org_id}-{room.room_uuid}'
    listed = await _livekit_rpc('ListParticipants', {'room': room_name})
    identities = {p['identity'] for p in listed.get('participants', []) if isinstance(p.get('identity'), str)}
    users = (await db.execute(select(User).join(TTCohortMember, TTCohortMember.user_id == User.id).where(TTCohortMember.org_id == room.org_id, TTCohortMember.cohort_id == room.cohort_id))).scalars().all()
    identities.update(participant_identity(room, user) for user in users)
    cutoff = int(utcnow().timestamp()) + 1
    for identity in identities:
        await _livekit_rpc('RemoveParticipant', {'room': room_name, 'identity': identity, 'revoke_token_ts': cutoff})
    room.media_enforcement_pending = False
    db.add(room)
    await db.flush()
