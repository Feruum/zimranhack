from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.thinktogether.learning import TTProgram, TTSkill, TTItem, TTExposure
from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom, TTRoomEvent
from src.services.thinktogether.rooms import (
    occurrence_utc, validate_schedule, require_cohort_member, require_room_member,
    apply_command, available_cohorts, room_view, board_access,
)


def test_schedule_rejects_nonexistent_and_requires_fold_for_ambiguous_time():
    validate_schedule('America/New_York', {'weekday': 6, 'local_time': '02:30', 'duration_minutes': 60})
    with pytest.raises(HTTPException) as missing:
        occurrence_utc('America/New_York', date(2026, 3, 8), '02:30')
    assert missing.value.status_code == 422
    with pytest.raises(HTTPException):
        occurrence_utc('America/New_York', date(2026, 11, 1), '01:30')
    assert occurrence_utc('America/New_York', date(2026, 11, 1), '01:30', 1).hour == 6


@pytest.fixture
async def room_setup(db, org, regular_user):
    from src.db.users import User
    regular_user = await db.get(User, regular_user.id)
    regular_user.email_verified = True
    db.add(regular_user)
    cohort = TTCohort(org_id=org.id, title='Real cohort', language='en', timezone='UTC', schedule={'weekday': 0, 'local_time': '18:00', 'duration_minutes': 60}, status='active')
    db.add(cohort)
    await db.flush()
    member = TTCohortMember(org_id=org.id, cohort_id=cohort.id, user_id=regular_user.id, status='active')
    db.add(member)
    room = TTRoom(org_id=org.id, cohort_id=cohort.id, title='Session', starts_at=datetime.now(timezone.utc), created_by=regular_user.id)
    db.add(room)
    await db.commit()
    return cohort, member, room, SimpleNamespace(org_id=org.id, user=regular_user, is_staff=False, can=lambda p: False)


async def test_membership_is_scoped_and_revocation_immediate(db, room_setup):
    cohort, member, room, ctx = room_setup
    assert (await require_cohort_member(db, ctx.org_id, ctx.user.id, cohort.id)).id == member.id
    assert (await require_room_member(db, ctx.org_id, ctx.user.id, room.id)).id == room.id
    with pytest.raises(HTTPException):
        await require_room_member(db, ctx.org_id + 1, ctx.user.id, room.id)
    member.status = 'revoked'
    db.add(member)
    await db.commit()
    with pytest.raises(HTTPException):
        await require_room_member(db, ctx.org_id, ctx.user.id, room.id)


async def test_availability_does_not_advertise_undersized_or_forming_cohort(db, room_setup):
    _, _, _, ctx = room_setup
    assert await available_cohorts(db, ctx.org_id) == []


async def test_command_is_idempotent_stale_safe_and_attempt_phase_is_private(db, room_setup, monkeypatch):
    from src.services.thinktogether import rooms
    async def access(*args, **kwargs):
        return None
    monkeypatch.setattr(rooms, 'require_paid', access)
    _, _, room, ctx = room_setup
    first = await apply_command(db, ctx, room.room_uuid, 'chat-one', 0, 'chat', {'text': 'hello'})
    repeated = await apply_command(db, ctx, room.room_uuid, 'chat-one', 0, 'chat', {'text': 'hello'})
    assert first == repeated
    assert first['revision'] == 1
    with pytest.raises(HTTPException) as stale:
        await apply_command(db, ctx, room.room_uuid, 'chat-two', 0, 'chat', {'text': 'stale'})
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException):
        await apply_command(db, ctx, room.room_uuid, 'chat-one', 0, 'chat', {'text': 'changed'})
    ctx.is_staff = True
    ctx.can = lambda p: True
    advanced = await apply_command(db, ctx, room.room_uuid, 'advance-one', 1, 'advance', {})
    assert advanced['phase'] == 'individual_attempt'
    with pytest.raises(HTTPException) as private:
        await apply_command(db, ctx, room.room_uuid, 'private-chat', 2, 'chat', {'text': 'answer'})
    assert private.value.status_code == 409
    events = (await db.execute(select(TTRoomEvent).where(TTRoomEvent.room_id == room.id).order_by(TTRoomEvent.sequence))).scalars().all()
    assert [event.sequence for event in events] == [1, 2]
    assert (await room_view(db, ctx, room.room_uuid))['revision'] == 2


async def test_board_acl_rechecks_phase_membership_and_paid_access(db, room_setup, monkeypatch):
    from src.services.thinktogether import rooms
    async def access(*args, **kwargs): pass
    monkeypatch.setattr(rooms, 'require_paid', access)
    _, member, room, ctx = room_setup
    room.board_uuid = 'board_test'
    room.phase = 'discussion'
    db.add(room)
    await db.commit()
    assert (await board_access(db, ctx, 'board_test'))['can_write'] is True
    room.phase = 'transfer'
    db.add(room)
    await db.commit()
    with pytest.raises(HTTPException) as private_board:
        await board_access(db, ctx, 'board_test')
    assert private_board.value.status_code == 403
    member.status = 'revoked'
    db.add(member)
    await db.commit()
    with pytest.raises(HTTPException):
        await board_access(db, ctx, 'board_test')

async def test_media_is_disabled_honestly_and_consent_is_explicit(db, room_setup, monkeypatch):
    from src.services.thinktogether import rooms
    async def access(*args, **kwargs): pass
    monkeypatch.setattr(rooms, 'require_paid', access)
    monkeypatch.delenv('LIVEKIT_URL', raising=False)
    monkeypatch.delenv('TT_LIVEKIT_URL', raising=False)
    _, _, room, ctx = room_setup
    with pytest.raises(HTTPException) as disabled:
        await rooms.media_token(db, ctx, room.room_uuid)
    assert disabled.value.status_code == 503
    monkeypatch.setenv('LIVEKIT_URL', 'wss://example.livekit.cloud')
    monkeypatch.setenv('LIVEKIT_API_KEY', 'test-key')
    monkeypatch.setenv('LIVEKIT_API_SECRET', 'test-secret-at-least-32-characters')
    # Joining WebRTC is separate from consent to STT/agent processing.
    assert (await rooms.media_token(db, ctx, room.room_uuid))['url'].startswith('wss://')
    await apply_command(db, ctx, room.room_uuid, 'consent-1', 0, 'consent_audio', {'granted': True, 'consent_version': 'audio-v1'})
    token = await rooms.media_token(db, ctx, room.room_uuid)
    import jwt
    claims = jwt.decode(token['token'], options={'verify_signature': False})
    assert claims['video']['room'] == f'tt-{ctx.org_id}-{room.room_uuid}'
    assert claims['sub'].endswith(ctx.user.user_uuid)
    assert claims['exp'] - claims['iat'] == 600


async def test_voice_context_excludes_profiles_and_rejects_unconsented_or_forged_speakers(db, room_setup, monkeypatch):
    from src.services.thinktogether import rooms
    async def access(*args, **kwargs): pass
    monkeypatch.setattr(rooms, 'require_paid', access)
    _, _, room, ctx = room_setup
    room.phase = 'discussion'
    db.add(room)
    await db.commit()
    assert (await rooms.voice_context(db, room.room_uuid))['participants'] == []
    await apply_command(db, ctx, room.room_uuid, 'consent-voice', 0, 'consent_audio', {'granted': True, 'consent_version': 'audio-v1'})
    context = await rooms.voice_context(db, room.room_uuid)
    assert 'profile' not in str(context)
    identity = context['participants'][0]['identity']
    with pytest.raises(HTTPException):
        await rooms.record_utterance(db, room.room_uuid, 'utterance-1', identity + 'forged', 'hello')
    first = await rooms.record_utterance(db, room.room_uuid, 'utterance-1', identity, 'hello')
    assert await rooms.record_utterance(db, room.room_uuid, 'utterance-1', identity, 'hello') == first
    await apply_command(db, ctx, room.room_uuid, 'consent-revoke', 1, 'consent_audio', {'granted': False, 'consent_version': 'audio-v1'})
    with pytest.raises(HTTPException):
        await rooms.record_utterance(db, room.room_uuid, 'utterance-2', identity, 'no consent')


async def test_shared_case_exposure_and_agent_events_are_durable(db, room_setup, monkeypatch):
    from src.db.thinktogether.learning import TTProgram, TTSkill, TTItem, TTExposure
    from src.services.thinktogether import rooms
    async def access(*args, **kwargs): pass
    monkeypatch.setattr(rooms, 'require_paid', access)
    cohort, _, room, ctx = room_setup
    program = TTProgram(org_id=ctx.org_id, title='Approved', status='published', is_sample=False)
    db.add(program)
    await db.flush()
    skill = TTSkill(org_id=ctx.org_id, program_id=program.id, code='shared', title='Shared skill')
    db.add(skill)
    await db.flush()
    item = TTItem(org_id=ctx.org_id, program_id=program.id, skill_id=skill.id, family_id='shared-case', title='Case', prompt='Discuss scope.', answer_key={'answer': 'never-expose'}, status='published', is_sample=False, approval_evidence='Expert reviewed', approved_by=ctx.user.id)
    db.add(item)
    await db.flush()
    cohort.program_id = program.id
    room.case_item_id = item.id
    room.phase = 'individual_attempt'
    db.add(cohort)
    db.add(room)
    await db.commit()
    ctx.can = lambda p: True
    before = await room_view(db, ctx, room.room_uuid)
    assert before.get('case') is None
    await apply_command(db, ctx, room.room_uuid, 'reveal-case', 0, 'advance', {})
    view = await room_view(db, ctx, room.room_uuid)
    assert view['case']['prompt'] == 'Discuss scope.'
    assert 'never-expose' not in str(view)
    exposures = (await db.execute(select(TTExposure).where(TTExposure.source_id == room.room_uuid))).scalars().all()
    assert len(exposures) == 1
    assert exposures[0].family_id == 'shared-case'
    result = await rooms.publish_agent_message(db, room.room_uuid, 'Consider the stakeholders.', 'voice-message-1')
    assert result == await rooms.publish_agent_message(db, room.room_uuid, 'Consider the stakeholders.', 'voice-message-1')
    assert result['id'] == 2
    assert (await room_view(db, ctx, room.room_uuid))['revision'] == 2


async def test_livekit_private_phase_removes_present_and_absent_identities_with_strict_cutoff(db, room_setup, monkeypatch):
    from src.services.thinktogether import rooms
    _, _, room, ctx = room_setup
    room.phase = 'transfer'
    db.add(room)
    await db.commit()
    monkeypatch.setenv('LIVEKIT_URL', 'wss://example.livekit.cloud')
    monkeypatch.setenv('LIVEKIT_API_KEY', 'test-key')
    monkeypatch.setenv('LIVEKIT_API_SECRET', 'test-secret-at-least-32-characters')
    calls = []
    async def rpc(method, payload):
        calls.append((method, payload))
        return {'participants': [{'identity': 'facilitator-agent'}]} if method == 'ListParticipants' else {}
    monkeypatch.setattr(rooms, '_livekit_rpc', rpc)
    await rooms.enforce_media_phase(db, SimpleNamespace(org_id=ctx.org_id, payload={'room_uuid': room.room_uuid}))
    removes = [p for method, p in calls if method == 'RemoveParticipant']
    assert len(removes) == 2
    assert all(p['revoke_token_ts'] > 0 for p in removes)

