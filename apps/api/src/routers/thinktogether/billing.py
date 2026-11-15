from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.thinktogether.billing import TTProductConfig
from src.db.thinktogether.learning import TTProgram
from src.services.thinktogether import billing
from src.services.thinktogether.billing_polar import verify_webhook
from src.services.thinktogether.common import TTContext, get_context, require_permission, utcnow

router = APIRouter(tags=["ThinkTogether billing"])


class CheckoutRequest(BaseModel):
    program_uuid: str
    cohort_uuid: str


class ProductRequest(BaseModel):
    program_uuid: str
    polar_product_id: str = Field(min_length=1, max_length=100)
    price_amount: int = Field(default=14900, ge=1, le=10000000)
    currency: str = Field(default="usd", pattern=r"^[a-z]{3}$")
    access_days: int = Field(default=56, ge=1, le=3650)
    enabled: bool = False


class ComplimentaryRequest(BaseModel):
    program_uuid: str
    user_uuid: str
    reason: str = Field(min_length=10, max_length=2000)
    access_days: int = Field(default=56, ge=1, le=3650)


async def program_row(db, org_id, program_uuid):
    program = (await db.exec(select(TTProgram).where(TTProgram.org_id == org_id,
        TTProgram.public_id == program_uuid))).first()
    if program is None:
        raise HTTPException(404, "Program not found")
    return program


@router.get("/billing/status")
async def status(program_uuid: str | None = None, ctx: TTContext = Depends(get_context),
    db: AsyncSession = Depends(get_db_session)):
    program_id = (await program_row(db, ctx.org_id, program_uuid)).id if program_uuid else None
    return await billing.billing_readiness(db, ctx.org_id, ctx.user.id, program_id)


@router.post("/billing/checkout", status_code=201)
async def checkout(body: CheckoutRequest, ctx: TTContext = Depends(get_context),
    db: AsyncSession = Depends(get_db_session)):
    program = await program_row(db, ctx.org_id, body.program_uuid)
    return await billing.create_checkout(db, ctx, program.id, body.cohort_uuid)


@router.put("/billing/products")
async def product(body: ProductRequest, ctx: TTContext = Depends(get_context),
    db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, "billing")
    program = await program_row(db, ctx.org_id, body.program_uuid)
    if body.enabled and (program.status != "published" or program.is_sample or not program.full_curriculum):
        raise HTTPException(409, "Only an approved complete published program can be sold")
    config = (await db.exec(select(TTProductConfig).where(TTProductConfig.org_id == ctx.org_id,
        TTProductConfig.program_id == program.id).with_for_update())).first()
    if config is None:
        config = TTProductConfig(org_id=ctx.org_id, program_id=program.id, polar_product_id=body.polar_product_id)
    for field in ("polar_product_id", "price_amount", "currency", "access_days", "enabled"):
        setattr(config, field, getattr(body, field))
    config.updated_at = utcnow()
    db.add(config)
    await db.commit()
    return {"program_uuid": program.public_id, "polar_product_id": config.polar_product_id,
        "price_amount": config.price_amount, "currency": config.currency,
        "access_days": config.access_days, "enabled": config.enabled}


@router.post("/billing/webhooks/polar", status_code=202)
async def webhook(request: Request, db: AsyncSession = Depends(get_db_session)):
    raw_body = await request.body()
    if len(raw_body) > 1024 * 1024:
        raise HTTPException(413, "Webhook body is too large")
    event = verify_webhook(raw_body, dict(request.headers))
    return await billing.accept_webhook(db, event, request.headers.get("webhook-id", ""))


@router.post("/billing/complimentary", status_code=201)
async def complimentary(body: ComplimentaryRequest, ctx: TTContext = Depends(get_context),
    db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, "billing")
    program = await program_row(db, ctx.org_id, body.program_uuid)
    grant = await billing.grant_complimentary_access(db,ctx,body.user_uuid,program.id,body.reason,body.access_days)
    return {"entitlement_uuid":grant.entitlement_uuid,"source":grant.source,"status":grant.status,
        "program_uuid":program.public_id,"user_uuid":body.user_uuid,"reason":grant.grant_reason,
        "starts_at":grant.starts_at,"expires_at":grant.expires_at}
