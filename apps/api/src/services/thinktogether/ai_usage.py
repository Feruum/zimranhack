"""Content-free operational usage, independent of learning/job transactions.

Prices are optional release configuration, never an asserted provider invoice.
SDK run usage includes its reported internal requests; failed runs can have
unknown usage and must never be presented as free calls.
"""
import hashlib
import json
import logging
import math
import os
import re
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import select

from src.db.thinktogether.core import TTAuditEvent
from src.services.thinktogether.common import as_utc, require_permission, utcnow

logger = logging.getLogger(__name__)
PURPOSES = frozenset({'reasoning.grade', 'reasoning.evaluation', 'facilitator.reasoning', 'content.generate'})
_MODEL = re.compile(r'[A-Za-z0-9_.:/@+\-]{1,160}\Z')
_RATES = {'input_usd_per_million', 'output_usd_per_million'}


def parse_pricing(raw):
    """Validate an explicit model -> USD per million input/output token map."""
    if not raw.strip():
        return {}
    try:
        prices = json.loads(raw)
        if not isinstance(prices, dict):
            raise ValueError()
        for model, rates in prices.items():
            if not _MODEL.fullmatch(model) or not isinstance(rates, dict) or set(rates) != _RATES:
                raise ValueError()
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in rates.values()):
                raise ValueError()
        return prices
    except (ValueError, TypeError, OverflowError):
        # Never include the environment value in exception/log text.
        raise ValueError('Invalid release AI pricing configuration') from None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _estimate(model, input_tokens, output_tokens):
    try:
        prices = parse_pricing(os.getenv('TT_AI_PRICING_JSON', ''))
        rates = prices.get(model)
        if rates is None or input_tokens is None or output_tokens is None:
            return None
        amount = (Decimal(input_tokens) * Decimal(str(rates['input_usd_per_million']))
                  + Decimal(output_tokens) * Decimal(str(rates['output_usd_per_million']))) / Decimal(1000000)
        usd = float(amount)
        if not math.isfinite(usd):
            raise ValueError()
        version = hashlib.sha256(json.dumps(prices, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return {'usd': usd, 'basis': 'release_configured_estimate', 'pricing_version': version}
    except Exception as exc:
        logger.warning('thinktogether_ai_pricing_unavailable', extra={'error_class': type(exc).__name__})
        return None


def make_usage_callback(*, org_id, requested_model, purpose, session_factory=None):
    """Build a callback with no user, attempt, prompt, grade or room identifier.

    The independent session opens only after the model call finishes. A new
    UUID is minted on every invocation, including paid calls whose grade is
    later rejected or retried. The default disposes its engine in this event
    loop, so Celery's next asyncio.run never reuses an asyncpg connection.
    The factory is injectable for database tests.
    """
    model = requested_model if isinstance(requested_model, str) and _MODEL.fullmatch(requested_model) else 'unavailable'
    safe_purpose = purpose if purpose in PURPOSES else 'reasoning.grade'

    async def record(result, *, duration_ms, status):
        input_tokens = output_tokens = requests = None
        if result is not None:
            try:
                usage = result.usage()
                input_tokens = _count(getattr(usage, 'input_tokens', None))
                output_tokens = _count(getattr(usage, 'output_tokens', None))
                requests = _count(getattr(usage, 'requests', None))
            except Exception as exc:
                logger.warning('thinktogether_ai_usage_unavailable', extra={'error_class': type(exc).__name__})
        details = {'requested_model': model, 'purpose': safe_purpose, 'call_uuid': str(uuid4()),
                   'input_tokens': input_tokens, 'output_tokens': output_tokens, 'provider_requests': requests,
                   'duration_ms': _count(duration_ms), 'status': 'succeeded' if status == 'succeeded' else 'failed',
                   'estimated_cost': _estimate(model, input_tokens, output_tokens)}
        try:
            factory = session_factory
            if factory is None:
                from src.services.thinktogether.jobs_worker import job_session
                factory = job_session
            async with factory() as db:
                db.add(TTAuditEvent(org_id=org_id, action='ai.usage', details=details,
                                    created_at=utcnow()))
                await db.commit()
        except Exception as exc:
            logger.warning('thinktogether_ai_usage_write_failed', extra={'error_class': type(exc).__name__})

    return record


def _bucket():
    return dict(calls=0, succeeded_calls=0, failed_calls=0, input_tokens=0, output_tokens=0,
                provider_requests=0, unknown_usage_calls=0, unpriced_calls=0,
                estimated_cost_usd=None, estimated_priced_cost_usd=None)


def _add(bucket, data):
    bucket['calls'] += 1
    bucket['succeeded_calls' if data.get('status') == 'succeeded' else 'failed_calls'] += 1
    counts = {key: _count(data.get(key)) for key in ('input_tokens', 'output_tokens', 'provider_requests')}
    for key, value in counts.items():
        if value is not None:
            bucket[key] += value
    if any(value is None for value in counts.values()):
        bucket['unknown_usage_calls'] += 1
    estimate = data.get('estimated_cost')
    usd = estimate.get('usd') if isinstance(estimate, dict) and estimate.get('basis') == 'release_configured_estimate' else None
    if type(usd) in (int, float) and math.isfinite(usd) and usd >= 0:
        bucket['estimated_priced_cost_usd'] = (bucket['estimated_priced_cost_usd'] or 0) + usd
    else:
        bucket['unpriced_calls'] += 1
    bucket['estimated_cost_usd'] = bucket['estimated_priced_cost_usd'] if not bucket['unpriced_calls'] else None


async def usage_summary(db, ctx, *, start, end):
    """Return organization aggregates only, over a positive range <=31 days."""
    require_permission(ctx, 'billing')
    start, end = as_utc(start), as_utc(end)
    if not timedelta(0) < end-start <= timedelta(days=31):
        raise HTTPException(422, 'Choose a positive usage range of at most 31 days')
    totals, groups = _bucket(), {}
    # Stream metadata only; no learner table is joined and no raw event escapes.
    result = await db.stream(select(TTAuditEvent.details).where(
        TTAuditEvent.org_id == ctx.org_id, TTAuditEvent.action == 'ai.usage',
        TTAuditEvent.created_at >= start,
        TTAuditEvent.created_at < end).execution_options(yield_per=500))
    try:
        async for data in result.scalars():
            model = data.get('requested_model', 'unavailable')
            model = model if isinstance(model, str) and _MODEL.fullmatch(model) else 'unavailable'
            purpose = data.get('purpose') if data.get('purpose') in PURPOSES else 'unavailable'
            bucket = groups.setdefault((model, purpose), _bucket())
            _add(totals, data)
            _add(bucket, data)
    finally:
        await result.close()
    return {'start': start.isoformat(), 'end': end.isoformat(), 'totals': totals,
            'groups': [dict(requested_model=model, purpose=purpose, **values)
                       for (model, purpose), values in sorted(groups.items())],
            'cost_basis': 'Release configured estimate in USD; unavailable counts and prices are excluded from the priced subtotal. This is not a provider invoice.',
            'token_basis': 'Reported provider counts; token and request subtotals exclude calls with unavailable counts.'}
