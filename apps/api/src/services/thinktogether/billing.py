"""Server-owned purchase terms and locked cohort seat allocation."""
import os
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.thinktogether.billing import TTEntitlement, TTPaymentOrder, TTProductConfig, TTSeatReservation, TTWebhookInbox
from src.db.thinktogether.rooms import TTCohort, TTCohortMember
from src.services.thinktogether.common import as_utc, utcnow
from src.services.thinktogether.billing_polar import PolarAdapter, provider_config
from src.services.thinktogether.jobs import JobDeferred, enqueue_job, _insert


async def has_active_access(db: AsyncSession, org_id: int, user_id: int, program_id=None) -> bool:
    now = utcnow()
    query = select(TTEntitlement.id).where(TTEntitlement.org_id == org_id,
        TTEntitlement.user_id == user_id, TTEntitlement.status == "active",
        TTEntitlement.starts_at <= now, TTEntitlement.expires_at > now)
    if program_id is not None:
        query = query.where(TTEntitlement.program_id == program_id)
    return (await db.exec(query.limit(1))).first() is not None


async def require_active_access(db, org_id, user_id, program_id=None):
    if not await has_active_access(db, org_id, user_id, program_id):
        raise HTTPException(403, "Active ThinkTogether access is required")


async def _seat_counts(db, cohort, exclude_reservation_id=None):
    members = (await db.exec(select(func.count()).select_from(TTCohortMember).where(
        TTCohortMember.org_id == cohort.org_id, TTCohortMember.cohort_id == cohort.id,
        TTCohortMember.status == "active"))).one()
    query = select(func.count()).select_from(TTSeatReservation).where(
        TTSeatReservation.org_id == cohort.org_id, TTSeatReservation.cohort_id == cohort.id,
        TTSeatReservation.status == "held", TTSeatReservation.expires_at > utcnow())
    if exclude_reservation_id is not None:
        query = query.where(TTSeatReservation.id != exclude_reservation_id)
    return members, (await db.exec(query)).one()


async def _real_member_count(db, cohort):
    from src.db.users import User
    return (await db.exec(select(func.count()).select_from(TTCohortMember).join(User,User.id == TTCohortMember.user_id).where(
        TTCohortMember.org_id == cohort.org_id,TTCohortMember.cohort_id == cohort.id,
        TTCohortMember.status == "active",User.email_verified == True))).one()


def _compatible(cohort, profile):
    if cohort.program_id != profile.program_id or cohort.language != profile.language or cohort.timezone != profile.timezone:
        return False
    try:
        exam = date.fromisoformat(profile.exam_date)
    except (TypeError, ValueError):
        return False
    if cohort.exam_window_start is None or cohort.exam_window_end is None:
        return False
    if not cohort.exam_window_start <= exam <= cohort.exam_window_end:
        return False
    # Slots are explicit weekly availability dictionaries in the profile's timezone.
    schedule = cohort.schedule or {}
    for slot in profile.schedule_slots or []:
        if not isinstance(slot, dict):
            continue
        if slot.get("weekday") == schedule.get("weekday") and slot.get("local_time") == schedule.get("local_time"):
            return True
    return False


async def available_cohorts(db, org_id, user_id, program_id=None):
    from src.db.thinktogether.learning import TTLearnerProfile
    query = select(TTLearnerProfile).where(TTLearnerProfile.org_id == org_id, TTLearnerProfile.user_id == user_id)
    if program_id is not None:
        query = query.where(TTLearnerProfile.program_id == program_id)
    profile = (await db.exec(query)).first()
    if profile is None:
        return []
    cohorts = (await db.exec(select(TTCohort).where(TTCohort.org_id == org_id,
        TTCohort.program_id == profile.program_id, TTCohort.status == "active",
        TTCohort.capacity >= 4, TTCohort.capacity <= 6))).all()
    available = []
    for cohort in cohorts:
        members, held = await _seat_counts(db, cohort)
        if await _real_member_count(db,cohort) >= 4 and members+held < cohort.capacity and _compatible(cohort, profile):
            available.append(cohort)
    return available


async def billing_readiness(db, org_id, user_id, program_id=None):
    from src.db.users import User
    from src.db.thinktogether.learning import TTProgram
    from src.services.thinktogether.learning import learning_readiness
    missing = []
    token, secret, environment = provider_config()
    if not token or not secret:
        missing.append("polar_credentials")
    try:
        import polar.v2026_10  # noqa: F401
    except ImportError:
        missing.append("polar_sdk")
    user = (await db.exec(select(User).where(User.id == user_id))).first()
    if user is None or not user.email_verified:
        missing.append("verified_email")
    learning = await learning_readiness(db, org_id, user_id)
    if not learning.get("ready"):
        missing.append("learning_readiness")
    from src.services.thinktogether.learning import launch_readiness
    curriculum = await launch_readiness(db,org_id)
    if not curriculum.get("ready"):
        missing.append("approved_curriculum_and_instruments")
    enabled = lambda name: os.getenv(name, "").lower() in {"1","true","yes"}
    for gate in ("TT_RELEASE_APPROVED", "TT_AI_EVALUATION_APPROVED", "TT_RESTORE_DRILL_PASSED"):
        if not enabled(gate):
            missing.append(gate.lower())
    if environment == "production" and not enabled("TT_POLAR_PRODUCTION_APPROVED"):
        missing.append("polar_production_approved")
    if environment == 'production':
        from src.services.thinktogether.release import provider_checks
        missing.extend(name for name, ready in provider_checks().items() if not ready)
    products_query = select(TTProductConfig).where(TTProductConfig.org_id == org_id, TTProductConfig.enabled == True)
    if program_id is not None:
        products_query = products_query.where(TTProductConfig.program_id == program_id)
    products = (await db.exec(products_query)).all()
    if not products:
        missing.append("configured_product")
    programs = {p.id: p for p in (await db.exec(select(TTProgram).where(TTProgram.org_id == org_id))).all()}
    approved = [p for p in products if p.program_id in programs and programs[p.program_id].status == "published"
        and not programs[p.program_id].is_sample and programs[p.program_id].full_curriculum and programs[p.program_id].approval_evidence]
    if products and not approved:
        missing.append("approved_complete_program")
    cohorts = await available_cohorts(db, org_id, user_id, program_id)
    if not cohorts:
        missing.append("compatible_real_cohort")
    grants_query = select(TTEntitlement).where(TTEntitlement.org_id == org_id, TTEntitlement.user_id == user_id)
    if program_id is not None:
        grants_query = grants_query.where(TTEntitlement.program_id == program_id)
    grants = (await db.exec(grants_query)).all()
    return {"checkout_enabled": not missing, "environment": environment, "missing": missing,
        "learning": learning, "has_active_access": await has_active_access(db, org_id, user_id, program_id),
        "products": [{"program_uuid": programs[p.program_id].public_id, "price_amount": p.price_amount,
            "currency": p.currency, "access_days": p.access_days} for p in products if p.program_id in programs],
        "entitlements": [{"entitlement_uuid":g.entitlement_uuid,"program_uuid":programs[g.program_id].public_id,
            "status":"expired" if g.status == "active" and as_utc(g.expires_at) <= utcnow() else g.status,
            "source":g.source,"starts_at":g.starts_at,"expires_at":g.expires_at} for g in grants if g.program_id in programs],
        "cohorts": [{"cohort_uuid": c.cohort_uuid, "title": c.title, "schedule": c.schedule,
            "timezone": c.timezone, "language": c.language} for c in cohorts]}


async def reserve_seat(db, org_id, user, product, cohort_uuid):
    """Always lock user then cohort. Cohort staff mutations must use this lock too."""
    from src.db.users import User
    from src.db.thinktogether.learning import TTLearnerProfile
    await db.exec(select(User).where(User.id == user.id).with_for_update())
    if await has_active_access(db, org_id, user.id, product.program_id):
        raise HTTPException(409, "You already have active access to this program")
    existing = (await db.exec(select(TTSeatReservation).where(TTSeatReservation.org_id == org_id,
        TTSeatReservation.user_id == user.id, TTSeatReservation.program_id == product.program_id,
        TTSeatReservation.status == "held", TTSeatReservation.expires_at > utcnow()))).first()
    if existing is not None:
        raise HTTPException(409, "A checkout reservation is already open; wait for it to expire")
    cohort = (await db.exec(select(TTCohort).where(TTCohort.org_id == org_id,
        TTCohort.cohort_uuid == cohort_uuid).with_for_update())).first()
    if cohort is None:
        raise HTTPException(404, "Cohort not found")
    profile = (await db.exec(select(TTLearnerProfile).where(TTLearnerProfile.org_id == org_id,
        TTLearnerProfile.user_id == user.id, TTLearnerProfile.program_id == product.program_id))).first()
    members, held = await _seat_counts(db, cohort)
    if (cohort.status != "active" or not 4 <= cohort.capacity <= 6 or await _real_member_count(db,cohort) < 4
        or members+held >= cohort.capacity or profile is None or not _compatible(cohort, profile)):
        raise HTTPException(409, "A compatible real cohort with an available seat is required")
    member = (await db.exec(select(TTCohortMember).where(TTCohortMember.org_id == org_id,
        TTCohortMember.cohort_id == cohort.id, TTCohortMember.user_id == user.id,
        TTCohortMember.status == "active"))).first()
    if member:
        raise HTTPException(409, "You already belong to this cohort")
    reservation = TTSeatReservation(org_id=org_id, user_id=user.id, user_uuid=user.user_uuid,
        program_id=product.program_id, cohort_id=cohort.id, product_config_id=product.id, polar_product_id=product.polar_product_id,
        price_amount=product.price_amount, currency=product.currency, access_days=product.access_days,
        expires_at=utcnow()+timedelta(minutes=30))
    db.add(reservation)
    await db.flush()
    return reservation


async def create_checkout(db, ctx, program_id, cohort_uuid, adapter=None):
    if not getattr(ctx.user, "email_verified", False):
        raise HTTPException(403, "Verify your email before purchasing")
    status = await billing_readiness(db, ctx.org_id, ctx.user.id, program_id)
    if not status["checkout_enabled"]:
        raise HTTPException(503, {"message": "Checkout is not ready", "missing": status["missing"]})
    product = (await db.exec(select(TTProductConfig).where(TTProductConfig.org_id == ctx.org_id,
        TTProductConfig.program_id == program_id, TTProductConfig.enabled == True))).one()
    adapter = adapter or PolarAdapter()
    await adapter.validate_product(product)
    reservation = await reserve_seat(db, ctx.org_id, ctx.user, product, cohort_uuid)
    # Commit hold before external request so any paid notification can correlate it.
    await db.commit()
    success_url = os.getenv("TT_CHECKOUT_SUCCESS_URL", "http://localhost:3000/thinktogether/billing?checkout=returned")
    try:
        checkout = await adapter.create_checkout(product, reservation, ctx.user, success_url)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Polar checkout is temporarily unavailable; your seat hold expires in 30 minutes") from exc
    reservation.polar_checkout_id = str(checkout["id"])
    reservation.checkout_url = checkout["url"]
    db.add(reservation)
    await db.commit()
    return {"checkout_url": reservation.checkout_url, "reservation_uuid": reservation.reservation_uuid,
        "expires_at": reservation.expires_at, "access_active": False}


async def _queue_refund(db, order, reservation):
    order.refund_requested = True
    reservation.status = "refund_pending"
    await enqueue_job(db, order.org_id, "billing.refund", {"order_id": order.id}, f"full-refund:{order.polar_order_id}")


async def apply_authoritative_order(db, data):
    """Only call with an order fetched using the server's Polar credential."""
    reservation_uuid = (data.get("metadata") or {}).get("tt_reservation_uuid")
    if not reservation_uuid:
        return  # A different product owned by this merchant is outside this domain.
    reservation = (await db.exec(select(TTSeatReservation).where(
        TTSeatReservation.reservation_uuid == reservation_uuid).with_for_update())).first()
    if reservation is None:
        raise ValueError("Unknown payment reservation")
    product = await db.get(TTProductConfig, reservation.product_config_id)
    customer = data.get("customer") or {}
    external_id = customer.get("external_id") or data.get("external_customer_id")
    if external_id != reservation.user_uuid or data.get("product_id") != (reservation.polar_product_id or product.polar_product_id):
        raise ValueError("Payment identity or product mismatch")
    if reservation.polar_checkout_id and data.get("checkout_id") != reservation.polar_checkout_id:
        raise ValueError("Payment checkout mismatch")
    if not reservation.polar_checkout_id and data.get("checkout_id"):
        reservation.polar_checkout_id = str(data["checkout_id"])
    currency = str(data.get("currency", "")).lower()
    amount = int(data.get("subtotal_amount", data.get("total_amount", 0)))
    if currency != reservation.currency.lower() or amount != reservation.price_amount:
        raise ValueError("Payment currency or price mismatch")
    order = (await db.exec(select(TTPaymentOrder).where(TTPaymentOrder.polar_order_id == str(data["id"])).with_for_update())).first()
    if order is None:
        other = (await db.exec(select(TTPaymentOrder).where(TTPaymentOrder.reservation_id == reservation.id))).first()
        if other:
            raise ValueError("Multiple orders for one reservation require billing review")
        order = TTPaymentOrder(org_id=reservation.org_id, user_id=reservation.user_id,
            program_id=reservation.program_id, reservation_id=reservation.id, polar_order_id=str(data["id"]))
        db.add(order)
        await db.flush()
    # Full refunds are terminal locally, even if an old worker ever observes stale state.
    previously_refunded = order.status == "refunded"
    order.status = "refunded" if previously_refunded else str(data.get("status", "pending"))
    order.total_amount = int(data.get("total_amount", 0))
    order.refunded_amount = max(order.refunded_amount, int(data.get("refunded_amount", 0)))
    order.currency, order.checked_at = currency, utcnow()
    grant = (await db.exec(select(TTEntitlement).where(TTEntitlement.order_id == order.id))).first()
    full_refund = order.status == "refunded" or (order.total_amount > 0 and order.refunded_amount >= order.total_amount)
    if full_refund:
        order.status, reservation.status = "refunded", "refunded"
        if grant:
            grant.status, grant.revoked_at = "revoked", utcnow()
            db.add(grant)
        cohort = (await db.exec(select(TTCohort).where(TTCohort.id == reservation.cohort_id,
            TTCohort.org_id == reservation.org_id).with_for_update())).one()
        member = (await db.exec(select(TTCohortMember).where(TTCohortMember.org_id == reservation.org_id,
            TTCohortMember.cohort_id == cohort.id, TTCohortMember.user_id == reservation.user_id))).first()
        if member and not await has_active_access(db, reservation.org_id, reservation.user_id, reservation.program_id):
            member.status = "revoked"
            db.add(member)
            from src.services.thinktogether.room_integrations import enqueue_member_revocation
            await enqueue_member_revocation(db, reservation.org_id, cohort.id, reservation.user_id,
                f"full-refund:{order.polar_order_id}")
    elif data.get("paid") is True and order.status in {"paid", "partially_refunded"} and grant is None:
        cohort = (await db.exec(select(TTCohort).where(TTCohort.id == reservation.cohort_id,
            TTCohort.org_id == reservation.org_id).with_for_update())).one()
        members, held = await _seat_counts(db, cohort, reservation.id)
        if order.refund_requested or cohort.status != "active" or await _real_member_count(db,cohort) < 4 or members+held >= cohort.capacity:
            await _queue_refund(db, order, reservation)
        else:
            member = (await db.exec(select(TTCohortMember).where(TTCohortMember.org_id == reservation.org_id,
                TTCohortMember.cohort_id == cohort.id, TTCohortMember.user_id == reservation.user_id))).first()
            if member is None:
                member = TTCohortMember(org_id=reservation.org_id, cohort_id=cohort.id, user_id=reservation.user_id)
            member.status = "active"
            db.add(member)
            await db.flush()
            from src.db.thinktogether.rooms import TTRoom
            from src.services.thinktogether.rooms import expose_case_to_members
            discussed_rooms = (await db.exec(select(TTRoom).where(TTRoom.org_id == reservation.org_id,
                TTRoom.cohort_id == cohort.id,TTRoom.phase.in_(["discussion","transfer","summary"])))).all()
            for room in discussed_rooms:
                await expose_case_to_members(db,room)
            now = utcnow()
            order.paid_at, reservation.status = now, "consumed"
            db.add(TTEntitlement(org_id=reservation.org_id, user_id=reservation.user_id,
                program_id=reservation.program_id, order_id=order.id, starts_at=now,
                expires_at=now+timedelta(days=reservation.access_days)))
    db.add(order)
    db.add(reservation)
    await db.flush()
    return order


async def accept_webhook(db, event, webhook_id):
    from src.db.organizations import Organization
    if not webhook_id:
        raise HTTPException(400, "webhook-id is required")
    org_slug = os.getenv("TT_ORG_SLUG", os.getenv("LEARNHOUSE_INITIAL_ORG_SLUG", "default"))
    org = (await db.exec(select(Organization).where(Organization.slug == org_slug))).first()
    if org is None:
        raise HTTPException(503, "ThinkTogether organization is not configured")
    kind, data = event.get("type", ""), event.get("data") or {}
    payload = {}
    if kind.startswith("order."):
        payload["order_id"] = str(data.get("id", ""))
    elif kind.startswith("refund."):
        payload["order_id"] = str(data.get("order_id", ""))
    stmt = _insert(db, TTWebhookInbox).values(org_id=org.id, webhook_id=webhook_id,
        event_type=kind, payload=payload, status="queued", received_at=utcnow())
    result = await db.execute(stmt.on_conflict_do_nothing(index_elements=["webhook_id"]).returning(TTWebhookInbox.id))
    inbox_id = result.scalar_one_or_none()
    if inbox_id is not None:
        await enqueue_job(db, org.id, "billing.webhook", {"inbox_id": inbox_id}, f"polar-webhook:{webhook_id}")
    # Durable inbox + processing job must both be committed BEFORE returning 202.
    await db.commit()
    return {"accepted": True, "duplicate": inbox_id is None}


async def process_webhook_job(db, job):
    inbox = (await db.exec(select(TTWebhookInbox).where(TTWebhookInbox.id == job.payload["inbox_id"],
        TTWebhookInbox.org_id == job.org_id).with_for_update())).one()
    if inbox.status == "processed":
        return
    if inbox.payload.get("order_id"):
        authoritative = await PolarAdapter().get_order(inbox.payload["order_id"])
        await apply_authoritative_order(db, authoritative)
    # Checkout-created/return redirect events never grant an entitlement.
    inbox.status, inbox.processed_at = "processed", utcnow()
    db.add(inbox)


async def refund_order_job(db, job):
    order = (await db.exec(select(TTPaymentOrder).where(TTPaymentOrder.id == job.payload["order_id"],
        TTPaymentOrder.org_id == job.org_id).with_for_update())).one()
    if not order.refund_requested:
        raise ValueError("Refund was not authorized by allocation policy")
    if order.status == "refunded":
        return
    adapter = PolarAdapter()
    refund = await adapter.refund_remaining(order.polar_order_id, job.idempotency_key)
    if refund:
        order.polar_refund_id = str(refund["id"])
    current = await apply_authoritative_order(db, await adapter.get_order(order.polar_order_id))
    if current is None or current.status != "refunded":
        # Commit the accepted request id with the deferred job. Raising here would
        # roll it back, while marking done would lose pending/failed settlement.
        return JobDeferred(delay_seconds=60)


async def reconcile_payments_job(db, job):
    adapter = PolarAdapter()
    orders = (await db.exec(select(TTPaymentOrder).where(TTPaymentOrder.org_id == job.org_id,
        TTPaymentOrder.status != "refunded", TTPaymentOrder.checked_at < utcnow()-timedelta(minutes=10)))).all()
    for order in orders:
        await apply_authoritative_order(db, await adapter.get_order(order.polar_order_id))
    holds = (await db.exec(select(TTSeatReservation).where(TTSeatReservation.org_id == job.org_id,
        TTSeatReservation.status.in_(["held", "expired"])))).all()
    for reservation in holds:
        for data in await adapter.orders_for_reservation(reservation):
            if (data.get("metadata") or {}).get("tt_reservation_uuid") != reservation.reservation_uuid:
                continue
            # A list result discovers the id; fetch current settlement state just
            # as webhook processing does before issuing any commercial access.
            await apply_authoritative_order(db, await adapter.get_order(str(data["id"])))
        if reservation.status == "held" and as_utc(reservation.expires_at) <= utcnow():
            reservation.status = "expired"
            db.add(reservation)


async def grant_complimentary_access(db, ctx, user_uuid, program_id, reason, access_days=56):
    """An explicit audited scholarship grant, never a pretend Polar payment."""
    from src.db.users import User
    from src.db.user_organizations import UserOrganization
    from src.db.thinktogether.learning import TTProgram
    from src.services.thinktogether.learning import learning_readiness
    from src.services.thinktogether.common import require_permission
    require_permission(ctx, "billing")
    if len(reason.strip()) < 10 or not 1 <= access_days <= 3650:
        raise HTTPException(422, "An audit reason and valid access duration are required")
    user = (await db.exec(select(User).where(User.user_uuid == user_uuid).with_for_update())).first()
    membership = (await db.exec(select(UserOrganization).where(UserOrganization.org_id == ctx.org_id,
        UserOrganization.user_id == user.id))).first() if user else None
    if user is None or membership is None:
        raise HTTPException(404, "Organization member not found")
    if not user.email_verified:
        raise HTTPException(409, "The member must verify their email first")
    if not (await learning_readiness(db, ctx.org_id, user.id)).get("ready"):
        raise HTTPException(409, "The member must complete learning readiness first")
    program = (await db.exec(select(TTProgram).where(TTProgram.id == program_id, TTProgram.org_id == ctx.org_id))).first()
    if program is None or program.status != "published" or program.is_sample or not program.full_curriculum or not program.approval_evidence:
        raise HTTPException(409, "An approved complete published program is required")
    if await has_active_access(db, ctx.org_id, user.id, program_id):
        raise HTTPException(409, "The member already has active access")
    now = utcnow()
    entitlement = TTEntitlement(org_id=ctx.org_id,user_id=user.id,program_id=program_id,source="complimentary",
        granted_by=ctx.user.id,grant_reason=reason.strip(),starts_at=now,expires_at=now+timedelta(days=access_days))
    db.add(entitlement)
    await db.flush()
    from src.db.thinktogether.core import TTAuditEvent
    db.add(TTAuditEvent(org_id=ctx.org_id,actor_user_id=ctx.user.id,action="billing.complimentary_grant",
        resource_id=entitlement.entitlement_uuid,details={"user_uuid":user_uuid,"program_uuid":program.public_id,
            "reason":reason.strip(),"access_days":access_days,"source":"complimentary"}))
    await db.commit()
    return entitlement
