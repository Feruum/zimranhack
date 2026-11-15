"""Official Polar SDK 1.1 / API 2026-10. No credential or provider error logging.

SDK contract: https://github.com/polarsource/polar/tree/main/sdk/python
Pin the dashboard webhook endpoint to the same 2026-10 API version.
"""
import dataclasses
import os
from contextlib import asynccontextmanager

from fastapi import HTTPException


def as_dict(value):
    if isinstance(value, dict):
        return value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("Unsupported Polar SDK response")


def provider_config():
    token = os.getenv("TT_POLAR_ACCESS_TOKEN", "")
    secret = os.getenv("TT_POLAR_WEBHOOK_SECRET", "")
    environment = os.getenv("TT_POLAR_ENVIRONMENT", "sandbox")
    if environment not in {"sandbox", "production"}:
        raise HTTPException(503, "Polar environment is not configured correctly")
    return token, secret, environment


@asynccontextmanager
async def polar_client():
    token, _, environment = provider_config()
    if not token:
        raise HTTPException(503, "Payments are disabled until a Polar merchant account is configured")
    try:
        from polar.v2026_10 import PolarAsync
    except ImportError as exc:
        raise HTTPException(503, "Polar SDK is not installed") from exc
    async with PolarAsync(token, environment=environment, timeout=25) as client:
        yield client


class PolarAdapter:
    async def validate_product(self, config):
        async with polar_client() as client:
            product = as_dict(await client.products.get(config.polar_product_id))
        prices = [p for p in product.get("prices", []) if not p.get("is_archived")]
        valid = any(p.get("amount_type") == "fixed" and p.get("price_amount") == config.price_amount
            and p.get("price_currency", "").lower() == config.currency.lower() for p in prices)
        if product.get("is_archived") or product.get("is_recurring") or not valid:
            raise HTTPException(503, "Polar product must have the configured one-time fixed price")

    async def create_checkout(self, product, reservation, user, success_url):
        async with polar_client() as client:
            result = await client.checkouts.create(products=[product.polar_product_id],
                external_customer_id=user.user_uuid, customer_email=str(user.email),
                metadata={"tt_reservation_uuid": reservation.reservation_uuid,
                    "tt_org_id": str(reservation.org_id), "tt_user_uuid": user.user_uuid},
                allow_discount_codes=False, allow_trial=False,
                success_url=success_url)
            return as_dict(result)

    async def get_order(self, order_id):
        async with polar_client() as client:
            return as_dict(await client.orders.get(order_id))

    async def orders_for_checkout(self, checkout_id):
        async with polar_client() as client:
            return [as_dict(order) async for order in client.orders.iter_list(checkout_id=checkout_id)]

    async def orders_for_reservation(self, reservation):
        """Recover a lost checkout response using immutable server metadata.

        A saved checkout id is an extra filter, never a prerequisite for recovery.
        Customer and reservation metadata filters are official 2026-10 SDK args.
        """
        filters = {"external_customer_id": reservation.user_uuid,
            "metadata": {"tt_reservation_uuid": reservation.reservation_uuid}}
        if reservation.polar_checkout_id:
            filters["checkout_id"] = reservation.polar_checkout_id
        async with polar_client() as client:
            return [as_dict(order) async for order in client.orders.iter_list(**filters)]

    async def refund_remaining(self, order_id, key):
        """Requery first: a retry after a lost response cannot over-refund an order.

        Polar caps the refundable remainder; the same order is serialized by our DB
        lock. An ambiguous request is retried only after rechecking pending refunds.
        """
        async with polar_client() as client:
            order = as_dict(await client.orders.get(order_id))
            remaining = int(order["total_amount"]) - int(order.get("refunded_amount", 0))
            if remaining <= 0 or order.get("status") == "refunded":
                return None
            refunds = [as_dict(item) async for item in client.refunds.iter_list(order_id=order_id)]
            existing = next((item for item in refunds if item.get("metadata", {}).get("tt_refund_key") == key
                and item.get("status") not in {"failed", "canceled"}), None)
            if existing:
                return existing
            result = await client.refunds.create(order_id=order_id, amount=remaining,
                reason="other", comment="ThinkTogether reserved cohort seat is unavailable",
                revoke_benefits=True, metadata={"tt_refund_key": key})
            return as_dict(result)


def verify_webhook(raw_body: bytes, headers: dict):
    _, secret, _ = provider_config()
    if not secret:
        raise HTTPException(503, "Polar webhook signing secret is not configured")
    try:
        from polar.v2026_10.webhooks import PolarWebhookError, PolarWebhookVerificationError, validate_event
    except ImportError as exc:
        raise HTTPException(503, "Polar SDK is not installed") from exc
    try:
        return as_dict(validate_event(raw_body, headers, secret))
    except PolarWebhookVerificationError as exc:
        raise HTTPException(403, "Invalid webhook signature") from exc
    except PolarWebhookError as exc:
        raise HTTPException(400, "Invalid webhook payload") from exc
