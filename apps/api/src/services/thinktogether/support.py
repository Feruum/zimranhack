"""Organization-scoped support conversations and serialized, replayable commands."""
import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import select

from src.db.thinktogether.core import TTAuditEvent
from src.db.thinktogether.learning import TTAttempt, TTDiagnosticRun, TTSupportCase
from src.services.thinktogether import notifications
from src.services.thinktogether.common import require_permission
from src.services.thinktogether.ids import as_utc, public_id, utcnow


def _text(value, label, maximum, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise HTTPException(422, f'{label} must contain 1–{maximum} characters')
    return value.strip()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _message(author, message):
    return {'id': public_id(), 'author': author, 'message': message, 'created_at': utcnow().isoformat()}


def _audit(db, ctx, ticket, action, details):
    db.add(TTAuditEvent(org_id=ctx.org_id, actor_user_id=ctx.user.id,
                       action=action, resource_id=ticket.public_id, details=details))


async def _project(db, ctx, ticket):
    """Whitelist conversation fields; never join learner identities or answers."""
    details = ticket.details or {}
    safe = {}
    if ticket.kind == 'item_shortage':
        if type(details.get('program_id')) is int:
            safe['program_id'] = details['program_id']
        if isinstance(details.get('reason'), str):
            safe['reason'] = details['reason'][:500]
        if isinstance(details.get('skill_ids'), list):
            safe['skill_ids'] = [v for v in details['skill_ids'][:100] if type(v) is int]
    messages = []
    for message in details.get('messages', []):
        if not isinstance(message, dict):
            continue
        if message.get('author') not in {'learner', 'staff', 'system'}:
            continue
        messages.append({k: message[k] for k in ('id', 'author', 'message', 'created_at') if k in message})
    attempt_id = details.get('attempt_id')
    if attempt_id and ticket.user_id != ctx.user.id:
        # Support permissions never grant access to another learner's cognitive evidence.
        attempt = (await db.execute(select(TTAttempt).where(TTAttempt.org_id == ctx.org_id,
            TTAttempt.user_id == ticket.user_id, TTAttempt.public_id == attempt_id))).scalars().first()
        if not attempt:
            attempt_id = None
        elif attempt.diagnostic_id:
            run = await db.get(TTDiagnosticRun, attempt.diagnostic_id)
            if run and run.kind == 'cognitive' and not ctx.can('psychometrics'):
                attempt_id = None
    return {'id': ticket.public_id, 'kind': ticket.kind,
            'title': details.get('title', 'Learning content unavailable' if ticket.kind == 'item_shortage' else 'Support request'),
            'status': ticket.status, 'created_at': as_utc(ticket.created_at).isoformat(),
            'attempt_id': attempt_id, 'messages': messages, 'details': safe}


async def list_tickets(db, ctx, *, staff=False):
    query = select(TTSupportCase).where(TTSupportCase.org_id == ctx.org_id)
    if staff:
        require_permission(ctx, 'support')
    else:
        query = query.where(TTSupportCase.user_id == ctx.user.id)
    tickets = (await db.execute(query.order_by(TTSupportCase.id.desc()).limit(200)
                               .execution_options(populate_existing=True))).scalars().all()
    return [await _project(db, ctx, ticket) for ticket in tickets]


async def create_ticket(db, ctx, idempotency_key, kind, title, message, attempt_id=None):
    key = _text(idempotency_key, 'Idempotency key', 128)
    title, message = _text(title, 'Title', 200), _text(message, 'Message', 6000)
    if kind not in {'learning', 'group', 'billing', 'assessment'}:
        raise HTTPException(422, 'Choose a supported ticket category')
    if attempt_id is not None:
        try:
            attempt_id = str(UUID(str(attempt_id)))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, 'Attempt identifier must be a UUID')
    request_hash = _hash({'kind': kind, 'title': title, 'message': message, 'attempt_id': attempt_id})
    # The unique insert serializes concurrent creation across API replicas. It
    # also ensures a replay cannot overwrite the original conversation.
    insert = postgresql.insert if db.bind.dialect.name == 'postgresql' else sqlite.insert
    if attempt_id:
        attempt = (await db.execute(select(TTAttempt).where(TTAttempt.org_id == ctx.org_id,
            TTAttempt.user_id == ctx.user.id, TTAttempt.public_id == attempt_id))).scalars().first()
        if not attempt:
            raise HTTPException(404, 'Attempt not found')
    identifier = public_id()
    await db.execute(insert(TTSupportCase).values(public_id=identifier, org_id=ctx.org_id,
        user_id=ctx.user.id, kind=kind, dedup_key=f'ticket:{key}', status='open', created_at=utcnow(),
        details={'title': title, 'attempt_id': attempt_id, '_create_hash': request_hash,
                 'messages': [_message('learner', message)], '_commands': {}})
        .on_conflict_do_nothing(index_elements=['org_id', 'user_id', 'dedup_key']))
    ticket = (await db.execute(select(TTSupportCase).where(TTSupportCase.org_id == ctx.org_id,
        TTSupportCase.user_id == ctx.user.id, TTSupportCase.dedup_key == f'ticket:{key}')
        .execution_options(populate_existing=True))).scalars().one()
    if ticket.details.get('_create_hash') != request_hash:
        await db.rollback()
        raise HTTPException(409, 'This idempotency key was already used for a different request')
    if ticket.public_id == identifier:
        _audit(db, ctx, ticket, 'support.created', {'kind': kind})
    result = await _project(db, ctx, ticket)
    await db.commit()
    return result


async def _locked_ticket(db, ctx, identifier, *, staff=False):
    if staff:
        require_permission(ctx, 'support')
    conditions = [TTSupportCase.org_id == ctx.org_id, TTSupportCase.public_id == identifier]
    if not staff and not ctx.can('support'):
        conditions.append(TTSupportCase.user_id == ctx.user.id)
    if db.bind.dialect.name == 'sqlite':
        # SQLite ignores SELECT FOR UPDATE. Acquire its write lock before
        # loading JSON so separate sessions cannot lose another reply.
        await db.execute(update(TTSupportCase).where(*conditions).values(status=TTSupportCase.status))
    ticket = (await db.execute(select(TTSupportCase).where(*conditions).with_for_update()
        .execution_options(populate_existing=True))).scalars().first()
    if not ticket:
        await db.rollback()
        raise HTTPException(404, 'Support ticket not found')
    return ticket


def _command(ticket, ctx, key, payload):
    key = f'{ctx.user.id}:{_text(key, "Idempotency key", 128)}'
    details = dict(ticket.details or {})
    commands = dict(details.get('_commands', {}))
    request_hash = _hash(payload)
    if key in commands:
        if commands[key] != request_hash:
            raise HTTPException(409, 'This idempotency key was already used for a different request')
        return None
    if len(commands) >= 2000:
        raise HTTPException(409, 'This ticket has reached its conversation limit; create a follow-up ticket')
    commands[key] = request_hash
    details['_commands'] = commands
    details['messages'] = list(details.get('messages', []))
    return details


async def _notify(db, ctx, ticket, key, title):
    if ticket.user_id != ctx.user.id:
        await notifications.notify_user(db, ctx.org_id, ticket.user_id,
            f'support:{ticket.public_id}:{ctx.user.id}:{key}', title, '/thinktogether/profile#support')


async def add_message(db, ctx, identifier, idempotency_key, message):
    message = _text(message, 'Message', 6000)
    ticket = await _locked_ticket(db, ctx, identifier)
    try:
        details = _command(ticket, ctx, idempotency_key, {'action': 'message', 'message': message})
        if details is not None:
            author = 'staff' if ctx.can('support') else 'learner'
            details['messages'].append(_message(author, message))
            ticket.details = details
            db.add(ticket)
            _audit(db, ctx, ticket, 'support.message_added', {'author': author})
            if author == 'staff':
                await _notify(db, ctx, ticket, idempotency_key, 'Support replied to your ticket')
        result = await _project(db, ctx, ticket)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def set_status(db, ctx, identifier, idempotency_key, status, message=None):
    require_permission(ctx, 'support')
    if status not in {'open', 'in_progress', 'resolved'}:
        raise HTTPException(422, 'Choose a supported ticket status')
    message = _text(message, 'Message', 6000, optional=True)
    ticket = await _locked_ticket(db, ctx, identifier, staff=True)
    try:
        details = _command(ticket, ctx, idempotency_key,
                           {'action': 'status', 'status': status, 'message': message})
        if details is not None:
            previous = ticket.status
            if message:
                details['messages'].append(_message('staff', message))
            ticket.details, ticket.status = details, status
            db.add(ticket)
            _audit(db, ctx, ticket, 'support.status_changed', {'previous_status': previous, 'status': status})
            if message or previous != status:
                await _notify(db, ctx, ticket, idempotency_key,
                    'Your support ticket was resolved' if status == 'resolved' else 'Your support ticket was updated')
        result = await _project(db, ctx, ticket)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise
