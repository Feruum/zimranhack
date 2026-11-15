"""Cohort discussions, self-only calendars and durable session reminders."""
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import select
from src.db.thinktogether.community import TTCohortMessage
from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom
from src.db.thinktogether.learning import TTLearnerProfile, TTPlan, TTReview
from src.db.users import User
from src.services.thinktogether.ids import as_utc, utcnow
from src.services.thinktogether.rooms import require_cohort_member
from src.services.thinktogether.billing import has_active_access, require_active_access


async def _cohort(db, ctx, cohort_uuid, *, posting=False):
    stmt = select(TTCohort).where(TTCohort.org_id == ctx.org_id, TTCohort.cohort_uuid == cohort_uuid)
    if posting:
        # Serializes same-cohort inserts so pagination cannot skip an earlier
        # uncommitted cursor. The unique key also protects SQLite and retries.
        stmt = stmt.with_for_update()
    cohort = (await db.execute(stmt)).scalar_one_or_none()
    if cohort is None or cohort.status == 'archived':
        raise HTTPException(404, 'Cohort not found')
    if not posting and ctx.can('groups'):
        return cohort
    await _participant(db, ctx, cohort)
    return cohort


async def _participant(db, ctx, cohort):
    user = await db.get(User, ctx.user.id, populate_existing=True)
    if user is None or not user.email_verified:
        raise HTTPException(403, 'A verified learner account is required')
    await require_cohort_member(db, ctx.org_id, user.id, cohort.id)
    if cohort.program_id is None:
        raise HTTPException(403, 'This cohort has no program access configured')
    await require_active_access(db, ctx.org_id, user.id, cohort.program_id)


async def _private_session(db, ctx, now=None):
    now = as_utc(now or utcnow())
    rooms = (await db.execute(select(TTRoom).join(TTCohortMember,
        (TTCohortMember.cohort_id == TTRoom.cohort_id) & (TTCohortMember.org_id == ctx.org_id)
    ).where(TTRoom.org_id == ctx.org_id, TTCohortMember.user_id == ctx.user.id,
        TTCohortMember.status == 'active', TTRoom.starts_at <= now).order_by(TTRoom.id).with_for_update(of=TTRoom)
        .execution_options(populate_existing=True))).scalars().all()
    # Lock current discussion rooms too: a concurrent transition into a private
    # phase must serialize before/after this message transaction.
    return any(r.phase in ('individual_attempt', 'transfer') and as_utc(r.starts_at) <= now < as_utc(r.starts_at)+timedelta(minutes=r.duration_minutes) for r in rooms)


async def _message(db, row):
    author = await db.get(User, row.user_id)
    name = (' '.join(filter(None, [author.first_name, author.last_name])).strip() or author.username) if author else 'Former member'
    return {'id': row.id, 'message_uuid': row.message_uuid, 'text': row.text,
        'author': {'id': author.user_uuid if author else None, 'name': name},
        'created_at': as_utc(row.created_at).isoformat()}


async def list_messages(db, ctx, cohort_uuid, after=0, limit=50):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, 'Use a nonnegative cursor and a limit from 1 to 100')
    cohort = await _cohort(db, ctx, cohort_uuid)
    if await _private_session(db, ctx):
        return {'messages': [], 'next_after': 0, 'has_more': False, 'can_read': False,
                'posting_blocked': 'Group discussion pauses during your scheduled independent attempt or transfer. It reopens after this phase ends.'}
    rows = (await db.execute(select(TTCohortMessage).where(TTCohortMessage.org_id == ctx.org_id,
        TTCohortMessage.cohort_id == cohort.id, TTCohortMessage.id > after).order_by(TTCohortMessage.id).limit(limit+1))).scalars().all()
    blocked = None
    try:
        await _participant(db, ctx, cohort)
    except HTTPException as error:
        if error.status_code != 403:
            raise
        blocked = 'Posting requires active cohort membership, verified email and access to this program.'
    page = rows[:limit]
    return {'messages': [await _message(db, row) for row in page],
            'next_after': page[-1].id if page else after, 'has_more': len(rows) > limit,
            'posting_blocked': blocked, 'can_read': True}


async def post_message(db, ctx, cohort_uuid, text, idempotency_key):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise HTTPException(422, 'Write a message of 1–4000 characters')
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise HTTPException(422, 'An idempotency key of 1–128 characters is required')
    cohort = await _cohort(db, ctx, cohort_uuid, posting=True)
    fingerprint = hashlib.sha256(text.encode('utf-8')).hexdigest()
    filters = (TTCohortMessage.org_id == ctx.org_id, TTCohortMessage.cohort_id == cohort.id,
               TTCohortMessage.user_id == ctx.user.id, TTCohortMessage.idempotency_key == idempotency_key)
    previous = (await db.execute(select(TTCohortMessage).where(*filters))).scalar_one_or_none()
    if previous:
        if previous.fingerprint != fingerprint:
            raise HTTPException(409, 'This message identifier was already used with different content')
        return await _message(db, previous)
    if await _private_session(db, ctx):
        raise HTTPException(409, 'Group discussion pauses during your scheduled independent attempt or transfer')
    from sqlalchemy.dialects import postgresql, sqlite
    from src.services.thinktogether.ids import public_id
    insert = postgresql.insert if db.bind.dialect.name == 'postgresql' else sqlite.insert
    await db.execute(insert(TTCohortMessage).values(org_id=ctx.org_id, cohort_id=cohort.id, user_id=ctx.user.id,
        message_uuid=public_id(), idempotency_key=idempotency_key, fingerprint=fingerprint,
        text=text.strip(), created_at=utcnow()).on_conflict_do_nothing(
            index_elements=['org_id', 'cohort_id', 'user_id', 'idempotency_key']))
    row = (await db.execute(select(TTCohortMessage).where(*filters))).scalar_one()
    if row.fingerprint != fingerprint:
        await db.rollback()
        raise HTTPException(409, 'This message identifier was already used with different content')
    await db.commit()
    return await _message(db, row)


async def calendar_events(db, ctx, start=None, end=None):
    start = start or utcnow()
    end = end or start+timedelta(days=31)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(422, 'Calendar range timestamps require a timezone offset')
    start, end = as_utc(start), as_utc(end)
    if end <= start or end-start > timedelta(days=31):
        raise HTTPException(422, 'Choose a calendar range of at most 31 days')
    profile = (await db.execute(select(TTLearnerProfile).where(TTLearnerProfile.org_id == ctx.org_id,
        TTLearnerProfile.user_id == ctx.user.id))).scalar_one_or_none()
    timezone_name = profile.timezone if profile else 'UTC'
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise HTTPException(422, 'Choose a valid IANA timezone in your profile')
    events = []
    rooms = (await db.execute(select(TTRoom, TTCohort).join(TTCohort, TTCohort.id == TTRoom.cohort_id).join(
        TTCohortMember, TTCohortMember.cohort_id == TTRoom.cohort_id).where(TTRoom.org_id == ctx.org_id,
        TTCohort.org_id == ctx.org_id, TTCohortMember.org_id == ctx.org_id,
        TTCohortMember.user_id == ctx.user.id, TTCohortMember.status == 'active', TTCohort.status != 'archived',
        TTRoom.starts_at >= start-timedelta(days=1), TTRoom.starts_at < end))).all()
    for room, cohort in rooms:
        finish = as_utc(room.starts_at)+timedelta(minutes=room.duration_minutes)
        if finish <= start or cohort.program_id is None or not await has_active_access(db, ctx.org_id, ctx.user.id, cohort.program_id):
            continue
        events.append({'id': f'session:{room.room_uuid}', 'kind': 'session', 'title': room.title,
            'starts_at': as_utc(room.starts_at).isoformat(), 'ends_at': finish.isoformat(), 'all_day': False,
            'href': f'/thinktogether/rooms/{room.room_uuid}'})
    reviews = (await db.execute(select(TTReview).where(TTReview.org_id == ctx.org_id, TTReview.user_id == ctx.user.id,
        TTReview.status == 'pending', TTReview.due_at >= start, TTReview.due_at < end))).scalars().all()
    for review in reviews:
        events.append({'id': f'review:{review.public_id}', 'kind': 'review', 'title': f'{review.interval_days}-day retrieval review',
            'starts_at': as_utc(review.due_at).isoformat(), 'ends_at': as_utc(review.due_at).isoformat(), 'all_day': False,
            'href': '/thinktogether/practice?'+urlencode({'review': review.public_id})})
    first_day = start.astimezone(zone).date().isoformat()
    last_day = (end.astimezone(zone).date()+timedelta(days=1)).isoformat()
    plans = (await db.execute(select(TTPlan).where(TTPlan.org_id == ctx.org_id, TTPlan.user_id == ctx.user.id,
        TTPlan.plan_date >= first_day, TTPlan.plan_date < last_day).order_by(TTPlan.plan_date, TTPlan.version.desc()))).scalars().all()
    seen = set()
    for plan in plans:
        if plan.plan_date in seen:
            continue
        seen.add(plan.plan_date)
        day = date.fromisoformat(plan.plan_date)
        midnight = datetime.combine(day, time.min, zone)
        tomorrow = datetime.combine(day+timedelta(days=1), time.min, zone)
        if as_utc(tomorrow) <= start or as_utc(midnight) >= end:
            continue
        for index, activity in enumerate(plan.activities):
            if not isinstance(activity, dict):
                continue
            kind = activity.get('kind', 'practice')
            href = '/thinktogether/program?'+urlencode({'lesson': activity.get('lesson_id', '')}) if kind == 'lesson' else '/thinktogether/practice?'+urlencode({
                'review' if activity.get('review_id') else 'skill': activity.get('review_id') or activity.get('skill_id') or ''})
            events.append({'id': f'plan:{plan.public_id}:{index}', 'kind': 'plan_activity',
                'title': str(activity.get('title') or ('Concept lesson' if kind == 'lesson' else 'Independent practice')),
                'starts_at': as_utc(midnight).isoformat(), 'ends_at': as_utc(tomorrow).isoformat(),
                'all_day': True, 'date': day.isoformat(), 'estimated_minutes': activity.get('minutes'), 'href': href})
    return {'timezone': timezone_name, 'range': {'start': start.isoformat(), 'end': end.isoformat()},
            'events': sorted(events, key=lambda e: (e['starts_at'], e['id']))}


def calendar_ics(calendar):
    def escape(value):
        return str(value).replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')
    def stamp(value):
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//ThinkTogether//Personal calendar//EN',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-TIMEZONE:'+escape(calendar['timezone'])]
    for event in calendar['events']:
        lines += ['BEGIN:VEVENT', 'UID:'+escape(event['id'])+'@thinktogether.local',
                  'DTSTAMP:'+utcnow().strftime('%Y%m%dT%H%M%SZ'), 'SUMMARY:'+escape(event['title'])]
        if event['all_day']:
            day = date.fromisoformat(event['date'])
            lines += ['DTSTART;VALUE=DATE:'+day.strftime('%Y%m%d'), 'DTEND;VALUE=DATE:'+(day+timedelta(days=1)).strftime('%Y%m%d')]
        else:
            lines += ['DTSTART:'+stamp(event['starts_at'])]
            if datetime.fromisoformat(event['ends_at']) > datetime.fromisoformat(event['starts_at']):
                lines += ['DTEND:'+stamp(event['ends_at'])]
        lines += ['X-THINKTOGETHER-PATH:'+escape(event['href']), 'END:VEVENT']
    lines.append('END:VCALENDAR')
    folded = []
    for line in lines:
        current = ''
        for character in line:
            if len((current+character).encode('utf-8')) > 75:
                folded.append(current)
                current = ' '
            current += character
        folded.append(current)
    return '\r\n'.join(folded)+'\r\n'


async def session_reminders_job(db, job, *, now=None):
    """The worker owns commit; notification uniqueness makes redelivery safe."""
    from src.services.thinktogether.notifications import notify_user
    now = as_utc(now or utcnow())
    rooms = (await db.execute(select(TTRoom, TTCohort).join(TTCohort, TTCohort.id == TTRoom.cohort_id).where(
        TTRoom.org_id == job.org_id, TTCohort.org_id == job.org_id, TTCohort.status == 'active',
        TTRoom.starts_at > now, TTRoom.starts_at <= now+timedelta(hours=24)))).all()
    for room, cohort in rooms:
        if cohort.program_id is None:
            continue
        window = '1h' if as_utc(room.starts_at)-now <= timedelta(hours=1) else '24h'
        members = (await db.execute(select(TTCohortMember).where(TTCohortMember.org_id == job.org_id,
            TTCohortMember.cohort_id == cohort.id, TTCohortMember.status == 'active'))).scalars().all()
        for member in members:
            try:
                await require_cohort_member(db, job.org_id, member.user_id, cohort.id)
            except HTTPException as error:
                if error.status_code == 403:
                    continue
                raise
            if not await has_active_access(db, job.org_id, member.user_id, cohort.program_id):
                continue
            title = f'{room.title} starts '+('within one hour' if window == '1h' else 'within 24 hours')
            await notify_user(db, job.org_id, member.user_id, f'session:{room.room_uuid}:{window}', title,
                              f'/thinktogether/rooms/{room.room_uuid}')
