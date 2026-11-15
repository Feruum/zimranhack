from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.fixture
async def billing_db(db):
    yield db


async def test_entitlement_scope_expiry_and_revocation(billing_db):
    from src.db.thinktogether.billing import TTEntitlement
    from src.services.thinktogether.billing import has_active_access, require_active_access
    from src.services.thinktogether.common import utcnow
    grant = TTEntitlement(org_id=1, user_id=2, program_id=3, order_id=4, starts_at=utcnow(), expires_at=utcnow()+timedelta(days=56))
    billing_db.add(grant)
    await billing_db.commit()
    assert await has_active_access(billing_db, 1, 2, 3)
    assert not await has_active_access(billing_db, 9, 2, 3)
    assert not await has_active_access(billing_db, 1, 2, 8)
    grant.status = "revoked"
    await billing_db.commit()
    with pytest.raises(HTTPException) as exc:
        await require_active_access(billing_db, 1, 2, 3)
    assert exc.value.status_code == 403


async def test_checkout_disabled_without_real_provider_configuration(billing_db, monkeypatch):
    from src.services.thinktogether.billing import billing_readiness
    monkeypatch.delenv("TT_POLAR_ACCESS_TOKEN", raising=False)
    status = await billing_readiness(billing_db, 1, 2)
    assert status["checkout_enabled"] is False
    assert "polar_credentials" in status["missing"]


async def test_paid_order_activates_membership_and_full_refund_revokes(billing_db):
    from src.db.users import User
    from src.db.thinktogether.billing import TTProductConfig, TTSeatReservation, TTEntitlement
    from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.billing import apply_authoritative_order, has_active_access
    from src.services.thinktogether.common import utcnow
    product = TTProductConfig(org_id=1, program_id=3, polar_product_id="product", enabled=True)
    cohort = TTCohort(org_id=1, program_id=3, title="Real", language="en", timezone="UTC", schedule={"weekday":1,"local_time":"18:00"}, capacity=6, status="active")
    billing_db.add(product)
    billing_db.add(cohort)
    await billing_db.flush()
    for user_id in range(10,14):
        billing_db.add(User(id=user_id,user_uuid=f"founder-{user_id}",username=f"founder{user_id}",first_name="Founding",last_name="Learner",email=f"founder{user_id}@example.com",email_verified=True))
        billing_db.add(TTCohortMember(org_id=1, cohort_id=cohort.id, user_id=user_id, status="active"))
    reservation = TTSeatReservation(org_id=1, user_id=2, user_uuid="user-uuid", program_id=3, cohort_id=cohort.id, product_config_id=product.id, expires_at=utcnow()+timedelta(minutes=30))
    billing_db.add(reservation)
    room = TTRoom(org_id=1,cohort_id=cohort.id,title="Connected classroom",starts_at=utcnow(),created_by=1)
    billing_db.add(room)
    await billing_db.commit()
    order = {"id":"polar-order","status":"paid","paid":True,"product_id":"product","total_amount":14900,"refunded_amount":0,"currency":"usd","customer":{"external_id":"user-uuid"},"metadata":{"tt_reservation_uuid":reservation.reservation_uuid}}
    await apply_authoritative_order(billing_db, order)
    await billing_db.commit()
    assert await has_active_access(billing_db, 1, 2, 3)
    members = (await billing_db.exec(select(TTCohortMember))).all()
    purchased_member = next(m for m in members if m.user_id == 2)
    assert len(members) == 5 and purchased_member.status == "active"
    # Repeated and out-of-order deliveries consult the same current provider state.
    await apply_authoritative_order(billing_db, order)
    await billing_db.commit()
    assert len((await billing_db.exec(select(TTEntitlement))).all()) == 1
    order.update(status="partially_refunded", refunded_amount=100)
    await apply_authoritative_order(billing_db, order)
    await billing_db.commit()
    assert await has_active_access(billing_db, 1, 2, 3)
    assert not (await billing_db.exec(select(TTJob).where(TTJob.kind == "room_member_revoked"))).all()
    order.update(status="refunded", refunded_amount=14900)
    await apply_authoritative_order(billing_db, order)
    await billing_db.commit()
    assert not await has_active_access(billing_db, 1, 2, 3)
    await billing_db.refresh(purchased_member)
    assert purchased_member.status == "revoked"
    revocation = (await billing_db.exec(select(TTJob).where(TTJob.kind == "room_member_revoked"))).one()
    assert revocation.payload == {"room_uuid":room.room_uuid,"user_id":2}
    await apply_authoritative_order(billing_db,order)
    await billing_db.commit()
    assert len((await billing_db.exec(select(TTJob).where(TTJob.kind == "room_member_revoked"))).all()) == 1


async def test_late_payment_on_full_cohort_queues_real_refund(billing_db):
    from src.db.users import User
    from src.db.thinktogether.billing import TTProductConfig, TTSeatReservation
    from src.db.thinktogether.jobs import TTJob
    from src.db.thinktogether.rooms import TTCohort, TTCohortMember
    from src.services.thinktogether.billing import apply_authoritative_order, has_active_access
    from src.services.thinktogether.common import utcnow
    product = TTProductConfig(org_id=1, program_id=3, polar_product_id="product", enabled=True)
    cohort = TTCohort(org_id=1, program_id=3, title="Full", language="en", timezone="UTC", schedule={}, capacity=4, status="active")
    billing_db.add_all([product, cohort])
    await billing_db.flush()
    for user_id in range(10,14):
        billing_db.add(User(id=user_id,user_uuid=f"founder-{user_id}",username=f"founder{user_id}",first_name="Founding",last_name="Learner",email=f"founder{user_id}@example.com",email_verified=True))
        billing_db.add(TTCohortMember(org_id=1, cohort_id=cohort.id, user_id=user_id, status="active"))
    reservation = TTSeatReservation(org_id=1,user_id=2,user_uuid="user-uuid",program_id=3,cohort_id=cohort.id,product_config_id=product.id,expires_at=utcnow()-timedelta(minutes=1))
    billing_db.add(reservation)
    await billing_db.commit()
    await apply_authoritative_order(billing_db,{"id":"late","paid":True,"status":"paid","product_id":"product","total_amount":14900,"refunded_amount":0,"currency":"usd","customer":{"external_id":"user-uuid"},"metadata":{"tt_reservation_uuid":reservation.reservation_uuid}})
    await billing_db.commit()
    assert not await has_active_access(billing_db,1,2,3)
    jobs = (await billing_db.exec(select(TTJob))).all()
    assert len(jobs)==1 and jobs[0].kind=="billing.refund"


async def test_webhook_inbox_deduplicates_and_durably_enqueues_before_acceptance(billing_db, org, monkeypatch):
    from src.db.thinktogether.billing import TTWebhookInbox, TTEntitlement
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.billing import accept_webhook
    monkeypatch.setenv("TT_ORG_SLUG", org.slug)
    event = {"type":"checkout.created","data":{"id":"no-grant"}}
    assert (await accept_webhook(billing_db,event,"delivery-1"))["duplicate"] is False
    assert (await accept_webhook(billing_db,event,"delivery-1"))["duplicate"] is True
    assert len((await billing_db.exec(select(TTWebhookInbox))).all()) == 1
    assert len((await billing_db.exec(select(TTJob))).all()) == 1
    assert len((await billing_db.exec(select(TTEntitlement))).all()) == 0


async def test_invalid_webhook_is_rejected_by_official_sdk(monkeypatch):
    pytest.importorskip("polar.v2026_10")
    from src.services.thinktogether.billing_polar import verify_webhook
    monkeypatch.setenv("TT_POLAR_WEBHOOK_SECRET", "whsec_test_signing_secret")
    with pytest.raises(HTTPException) as exc:
        verify_webhook(b'{"type":"order.paid"}', {"webhook-id":"forged","webhook-timestamp":"0","webhook-signature":"v1,forged"})
    assert exc.value.status_code == 403


async def test_complimentary_grant_has_real_audit_and_no_provider_order(billing_db, org, admin_user, regular_user, monkeypatch):
    from src.db.users import User
    from src.db.thinktogether.learning import TTProgram
    from src.db.thinktogether.billing import TTEntitlement, TTPaymentOrder
    from src.services.thinktogether.billing import grant_complimentary_access
    from src.services.thinktogether.common import TTContext
    from src.services.thinktogether import learning
    stored_user = await billing_db.get(User,regular_user.id)
    stored_user.email_verified = True
    program = TTProgram(org_id=org.id,title="Approved program",status="published",is_sample=False,full_curriculum=True,approval_evidence="expert approval")
    billing_db.add_all([stored_user, program])
    await billing_db.commit()
    async def ready(*args):
        return {"ready":True}
    monkeypatch.setattr(learning,"learning_readiness",ready)
    ctx = TTContext(user=admin_user,org_id=org.id,is_staff=True,staff_roles=frozenset({"admin"}))
    entitlement = await grant_complimentary_access(billing_db,ctx,regular_user.user_uuid,program.id,"Founding cohort scholarship",56)
    assert entitlement.source == "complimentary"
    assert entitlement.granted_by == admin_user.id
    assert entitlement.order_id is None
    assert (await billing_db.exec(select(TTPaymentOrder))).all() == []


async def test_checkout_rejects_unverified_user_without_contacting_provider(billing_db):
    from src.services.thinktogether.billing import create_checkout
    ctx = SimpleNamespace(org_id=1,user=SimpleNamespace(id=2,email_verified=False))
    with pytest.raises(HTTPException) as exc:
        await create_checkout(billing_db,ctx,3,"cohort")
    assert exc.value.status_code == 403


async def _recovery_reservation(db):
    from src.db.users import User
    from src.db.thinktogether.billing import TTProductConfig, TTSeatReservation
    from src.db.thinktogether.rooms import TTCohort, TTCohortMember
    from src.services.thinktogether.ids import utcnow
    product = TTProductConfig(org_id=1, program_id=3, polar_product_id="product", enabled=True)
    cohort = TTCohort(org_id=1, program_id=3, title="Recovery", language="en", timezone="UTC", schedule={}, capacity=6, status="active")
    db.add_all([product, cohort])
    await db.flush()
    for uid in range(10, 14):
        db.add(User(id=uid, user_uuid=f"founder-{uid}", username=f"founder{uid}", first_name="Founding", last_name="Learner", email=f"founder{uid}@example.com", email_verified=True))
        db.add(TTCohortMember(org_id=1, cohort_id=cohort.id, user_id=uid, status="active"))
    reservation = TTSeatReservation(org_id=1, user_id=2, user_uuid="user-uuid", program_id=3, cohort_id=cohort.id,
        product_config_id=product.id, polar_product_id="product", expires_at=utcnow()-timedelta(minutes=1))
    db.add(reservation)
    await db.commit()
    data = {"id":"recovered-order", "checkout_id":"lost-checkout", "status":"paid", "paid":True, "product_id":"product",
        "total_amount":14900, "refunded_amount":0, "currency":"usd", "customer":{"external_id":"user-uuid"},
        "metadata":{"tt_reservation_uuid":reservation.reservation_uuid}}
    return reservation, data


async def test_reconciliation_recovers_ambiguous_checkout_without_granting_other_customer_orders(billing_db, monkeypatch):
    from src.services.thinktogether import billing
    from src.db.thinktogether.billing import TTEntitlement, TTPaymentOrder
    reservation, data = await _recovery_reservation(billing_db)
    seen = []
    class Adapter:
        async def orders_for_reservation(self, hold):
            seen.append((hold.user_uuid, hold.reservation_uuid, hold.polar_checkout_id))
            unrelated = dict(data, id="another-order", metadata={"tt_reservation_uuid":"unrelated"})
            return [unrelated, data]
        async def get_order(self, order_id):
            assert order_id == data["id"]
            return data
    monkeypatch.setattr(billing, "PolarAdapter", Adapter)
    await billing.reconcile_payments_job(billing_db, SimpleNamespace(org_id=1))
    await billing_db.commit()
    assert seen == [("user-uuid", reservation.reservation_uuid, None)]
    assert await billing.has_active_access(billing_db,1,2,3)
    await billing_db.refresh(reservation)
    assert reservation.polar_checkout_id == "lost-checkout"
    assert len((await billing_db.exec(select(TTEntitlement))).all()) == 1
    assert len((await billing_db.exec(select(TTPaymentOrder))).all()) == 1
    await billing.reconcile_payments_job(billing_db, SimpleNamespace(org_id=1))
    await billing_db.commit()
    assert len((await billing_db.exec(select(TTEntitlement))).all()) == 1


async def test_pending_refund_persists_request_and_polls_until_authoritative_confirmation(billing_db, monkeypatch):
    from src.services.thinktogether import billing
    from src.services.thinktogether.jobs import enqueue_job, run_job
    from src.services.thinktogether.ids import utcnow
    from src.db.thinktogether.billing import TTPaymentOrder
    reservation, data = await _recovery_reservation(billing_db)
    # Allocation failed, so there is no entitlement to revoke. A provider-accepted
    # request is not equivalent to a completed monetary refund.
    reservation.status = "refund_pending"
    order = TTPaymentOrder(org_id=1, user_id=2, program_id=3, reservation_id=reservation.id,
        polar_order_id=data["id"], status="paid", total_amount=14900, refund_requested=True)
    billing_db.add(order)
    await billing_db.flush()
    job = await enqueue_job(billing_db,1,"billing.refund",{"order_id":order.id},"refund-test")
    await billing_db.commit()
    calls = []
    class Adapter:
        async def refund_remaining(self, order_id, key):
            calls.append((order_id,key))
            return {"id":"refund-request", "status":"pending"}
        async def get_order(self, order_id):
            return data
    monkeypatch.setattr(billing,"PolarAdapter",Adapter)
    assert await run_job(billing_db,job.id,{"billing.refund":billing.refund_order_job})
    await billing_db.refresh(order)
    await billing_db.refresh(job)
    assert order.polar_refund_id == "refund-request"
    assert job.status == "retry" and job.completed_at is None and job.attempts == 0
    assert not await billing.has_active_access(billing_db,1,2,3)
    data.update(status="refunded",refunded_amount=14900)
    job.available_at = utcnow()-timedelta(seconds=1)
    await billing_db.commit()
    assert await run_job(billing_db,job.id,{"billing.refund":billing.refund_order_job})
    await billing_db.refresh(job)
    await billing_db.refresh(order)
    assert job.status == "done" and order.status == "refunded"


@pytest.mark.parametrize("prior_status,expected_creates", [("pending",0),("failed",1),("canceled",1)])
async def test_refund_adapter_reuses_pending_and_retries_failed_requests(monkeypatch, prior_status, expected_creates):
    from contextlib import asynccontextmanager
    from src.services.thinktogether import billing_polar
    created = []
    class Orders:
        async def get(self, order_id):
            assert order_id == "order"
            return {"status":"paid","total_amount":14900,"refunded_amount":100}
    class Refunds:
        async def iter_list(self, **kwargs):
            assert kwargs == {"order_id":"order"}
            yield {"id":"prior","status":prior_status,"metadata":{"tt_refund_key":"stable-key"}}
        async def create(self, **kwargs):
            created.append(kwargs)
            return {"id":"new","status":"pending"}
    @asynccontextmanager
    async def client():
        yield SimpleNamespace(orders=Orders(),refunds=Refunds())
    monkeypatch.setattr(billing_polar,"polar_client",client)
    result = await billing_polar.PolarAdapter().refund_remaining("order","stable-key")
    assert result["id"] == ("new" if expected_creates else "prior")
    assert len(created) == expected_creates
    if created:
        assert created[0]["amount"] == 14800
        assert created[0]["metadata"] == {"tt_refund_key":"stable-key"}


async def test_recovery_adapter_filters_customer_and_server_reservation_metadata(monkeypatch):
    from contextlib import asynccontextmanager
    from src.services.thinktogether import billing_polar
    calls = []
    class Orders:
        async def iter_list(self, **kwargs):
            calls.append(kwargs)
            yield {"id":"found"}
    @asynccontextmanager
    async def client():
        yield SimpleNamespace(orders=Orders())
    monkeypatch.setattr(billing_polar,"polar_client",client)
    hold = SimpleNamespace(user_uuid="learner", reservation_uuid="server-hold", polar_checkout_id=None)
    assert await billing_polar.PolarAdapter().orders_for_reservation(hold) == [{"id":"found"}]
    assert calls == [{"external_customer_id":"learner","metadata":{"tt_reservation_uuid":"server-hold"}}]
