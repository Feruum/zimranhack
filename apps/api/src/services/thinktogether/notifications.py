import html
import os

import httpx
from sqlmodel import select

from src.db.thinktogether.core import TTNotification, TTNotificationPreference
from src.db.users import User
from src.services.thinktogether.ids import utcnow


async def notify_user(db, org_id, user_id, key, title, href):
    if not href.startswith('/thinktogether/') or href.startswith('//'):
        raise ValueError('Notification links must remain inside ThinkTogether')
    from sqlalchemy.dialects import postgresql, sqlite
    insert = postgresql.insert if db.bind.dialect.name == 'postgresql' else sqlite.insert
    await db.execute(insert(TTNotification).values(org_id=org_id, user_id=user_id, dedup_key=key,
        title=title, href=href, read=False, emailed=False, created_at=utcnow()).on_conflict_do_nothing(
            index_elements=['org_id', 'user_id', 'dedup_key']))
    notification = (await db.execute(select(TTNotification).where(TTNotification.org_id == org_id,
        TTNotification.user_id == user_id, TTNotification.dedup_key == key))).scalars().one()
    preference = (await db.execute(select(TTNotificationPreference).where(
        TTNotificationPreference.org_id == org_id, TTNotificationPreference.user_id == user_id,
    ))).scalars().first()
    if preference and preference.email_reminders and os.getenv('LEARNHOUSE_RESEND_API_KEY'):
        from src.services.thinktogether.jobs import enqueue_job
        await enqueue_job(db, org_id, 'notification.email', {'notification_id': notification.id}, f'email:{notification.id}')
    return notification


async def email_job(db, job):
    notification = await db.get(TTNotification, job.payload['notification_id'])
    if not notification or notification.org_id != job.org_id or notification.emailed:
        return
    preference = (await db.execute(select(TTNotificationPreference).where(
        TTNotificationPreference.org_id == job.org_id,
        TTNotificationPreference.user_id == notification.user_id,
    ))).scalars().first()
    user = await db.get(User, notification.user_id)
    if not preference or not preference.email_reminders or not user or not user.email_verified:
        return
    key, sender = os.getenv('LEARNHOUSE_RESEND_API_KEY'), os.getenv('LEARNHOUSE_SYSTEM_EMAIL_ADDRESS')
    if not key or not sender:
        raise RuntimeError('Transactional email is not configured')
    base = os.getenv('NEXTAUTH_URL', '').rstrip('/')
    if not base.startswith(('https://', 'http://localhost:')):
        raise RuntimeError('A trusted frontend URL is required')
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post('https://api.resend.com/emails', headers={
            'Authorization': f'Bearer {key}', 'Idempotency-Key': f'tt-notification-{notification.id}',
        }, json={'from': sender, 'to': [str(user.email)], 'subject': notification.title,
                 'html': '<p>' + html.escape(notification.title) + '</p><p><a href="' +
                    html.escape(base + notification.href, quote=True) + '">Open your learning workspace</a></p>'})
        if response.status_code >= 400:
            raise RuntimeError('Email provider rejected delivery')
    notification.emailed = True
    db.add(notification)
