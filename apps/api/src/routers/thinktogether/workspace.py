"""Workspace identity, staff administration and explicit release gates."""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.core.events.database import get_db_session
from src.db.thinktogether.core import TTStaffGrant, TTAuditEvent, TTResourceGrant
from src.db.thinktogether.learning import TTProgram, TTInstrument
from src.db.thinktogether.rooms import TTCohort, TTCohortMember
from src.db.users import User
from src.security.org_auth import require_org_membership
from src.services.thinktogether.common import TTContext, get_context, require_permission

router = APIRouter()


@router.get('/bootstrap')
async def bootstrap(ctx: TTContext = Depends(get_context)):
    return {
        'name': 'ThinkTogether', 'org_id': ctx.org_id,
        'user': {'id': ctx.user.user_uuid, 'name': ctx.user.first_name or ctx.user.username,
                 'email_verified': ctx.user.email_verified},
        'is_staff': ctx.is_staff,
        'permissions': sorted(p for p in ('content', 'groups', 'support', 'psychometrics', 'billing') if ctx.can(p)),
        'source_url': os.getenv('TT_SOURCE_URL', '/thinktogether-source.tar.gz'),
    }


@router.get('/readiness')
async def readiness(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    if not ctx.is_staff:
        raise HTTPException(403, 'Staff workspace required')
    programs = (await db.execute(select(TTProgram).where(
        TTProgram.org_id == ctx.org_id, TTProgram.status == 'published',
        TTProgram.is_sample == False, TTProgram.full_curriculum == True,
    ))).scalars().all()
    instruments = (await db.execute(select(TTInstrument).where(
        TTInstrument.org_id == ctx.org_id, TTInstrument.status == 'published',
        TTInstrument.kind == 'cognitive', TTInstrument.is_sample == False,
    ))).scalars().all()
    cohorts = (await db.execute(select(TTCohort).where(
        TTCohort.org_id == ctx.org_id, TTCohort.status == 'active',
    ))).scalars().all()
    group_count = 0
    for cohort in cohorts:
        count = (await db.execute(select(func.count(TTCohortMember.id)).join(
            User, User.id == TTCohortMember.user_id,
        ).where(TTCohortMember.cohort_id == cohort.id, TTCohortMember.org_id == ctx.org_id,
                TTCohortMember.status == 'active', User.email_verified == True))).scalar_one()
        group_count += int(4 <= count <= cohort.capacity)
    ai = get_learnhouse_config().ai_config
    enabled = lambda key: os.getenv(key, '').lower() in {'1', 'true', 'yes'}
    checks = {
        'approved_curriculum': any(p.approval_evidence for p in programs),
        'approved_cognitive_instrument': any(i.license_reference and i.approval_evidence for i in instruments),
        'real_compatible_groups': group_count > 0,
        'polar_configured': bool(os.getenv('TT_POLAR_ACCESS_TOKEN') and os.getenv('TT_POLAR_WEBHOOK_SECRET')),
        'polar_production_approved': enabled('TT_POLAR_PRODUCTION_APPROVED'),
        'ai_evaluation_approved': enabled('TT_AI_EVALUATION_APPROVED'),
        'restore_drill_passed': enabled('TT_RESTORE_DRILL_PASSED'),
        'release_approved': enabled('TT_RELEASE_APPROVED'),
    }
    from src.services.thinktogether.learning import launch_readiness
    from src.services.thinktogether.release import provider_checks
    checks.update((await launch_readiness(db, ctx.org_id))['gates'])
    checks.update(provider_checks())
    return {'ready': all(checks.values()), 'checks': checks, 'blocking': [k for k, v in checks.items() if not v],
            'active_groups': group_count, 'pilot_capacity': 24}


class StaffGrantInput(BaseModel):
    user_uuid: str = Field(min_length=1, max_length=100)
    role: str
    active: bool = True


@router.put('/staff/grants')
async def staff_grant(body: StaffGrantInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    if 'admin' not in ctx.staff_roles:
        raise HTTPException(403, 'An organization administrator is required')
    if body.role not in {'expert', 'operator', 'psychometric', 'billing'}:
        raise HTTPException(422, 'Unknown staff responsibility')
    user = (await db.execute(select(User).where(User.user_uuid == body.user_uuid))).scalars().first()
    if not user:
        raise HTTPException(404, 'User not found')
    await require_org_membership(user.id, ctx.org_id, db)
    grant = (await db.execute(select(TTStaffGrant).where(
        TTStaffGrant.org_id == ctx.org_id, TTStaffGrant.user_id == user.id, TTStaffGrant.role == body.role,
    ))).scalars().first()
    if body.active and not grant:
        db.add(TTStaffGrant(org_id=ctx.org_id, user_id=user.id, role=body.role))
    elif not body.active and grant:
        await db.delete(grant)
    db.add(TTAuditEvent(org_id=ctx.org_id, actor_user_id=ctx.user.id, action='staff.grant' if body.active else 'staff.revoke',
                        resource_id=user.user_uuid, details={'role': body.role}))
    await db.commit()
    return {'user_id': user.user_uuid, 'role': body.role, 'active': body.active}


class ResourceInput(BaseModel):
    program_id: str
    resource_uuid: str = Field(min_length=1, max_length=100)


@router.get('/staff/users')
async def member_directory(search: str = '', ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    if not (ctx.can('groups') or ctx.can('support')):
        raise HTTPException(403, 'Member administration is required')
    from src.db.user_organizations import UserOrganization
    query = select(User).join(UserOrganization, UserOrganization.user_id == User.id).where(UserOrganization.org_id == ctx.org_id)
    if search:
        from sqlalchemy import or_
        term = '%' + search[:100].replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.where(or_(User.username.ilike(term, escape='\\'), User.email.ilike(term, escape='\\')))
    users = (await db.execute(query.order_by(User.id).limit(100))).scalars().unique().all()
    return [{'id': u.user_uuid, 'user_uuid': u.user_uuid, 'name': u.first_name or u.username,
             'email': str(u.email), 'email_verified': u.email_verified} for u in users]


@router.get('/me/attempts')
async def attempt_history(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    from src.db.thinktogether.learning import TTAttempt, TTItem, TTMockRun, TTDiagnosticRun
    from src.services.thinktogether.learning_evidence import latest_evaluation
    rows = (await db.execute(select(TTAttempt).where(TTAttempt.org_id == ctx.org_id,
        TTAttempt.user_id == ctx.user.id).order_by(TTAttempt.id.desc()).limit(100))).scalars().all()
    finished = (await db.execute(select(TTMockRun).where(TTMockRun.org_id == ctx.org_id,
        TTMockRun.user_id == ctx.user.id, TTMockRun.status == 'completed'))).scalars().all()
    finished_attempts = {a for run in finished for a in run.attempt_ids}
    result = []
    for attempt in rows:
        item = await db.get(TTItem, attempt.item_id)
        evaluation = await latest_evaluation(db, attempt.id)
        hidden = attempt.phase == 'mock' and attempt.public_id not in finished_attempts
        assessment_kind = None
        if attempt.diagnostic_id:
            diagnostic = await db.get(TTDiagnosticRun, attempt.diagnostic_id)
            assessment_kind = diagnostic.kind if diagnostic else None
            hidden = hidden or bool(diagnostic and diagnostic.kind == 'cognitive')
        result.append({'id': attempt.public_id, 'title': item.title if item else 'Practice task',
            'assessment_kind': assessment_kind,
            'phase': attempt.phase, 'created_at': attempt.created_at, 'assisted': attempt.assisted,
            'score': evaluation.score if evaluation and not hidden else None,
            'status': evaluation.status if evaluation and not hidden else 'in_progress'})
    return result


@router.get('/me/mock-runs')
async def mock_history(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    from src.db.thinktogether.learning import TTMockRun
    rows = (await db.execute(select(TTMockRun).where(TTMockRun.org_id == ctx.org_id,
        TTMockRun.user_id == ctx.user.id).order_by(TTMockRun.id.desc()).limit(50))).scalars().all()
    return [{'id': r.public_id, 'status': r.status, 'deadline': r.deadline, 'created_at': r.created_at,
             'score': r.score if r.status == 'completed' else None} for r in rows]


@router.get('/me/cohorts')
async def my_cohorts(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    cohorts = (await db.execute(select(TTCohort).join(TTCohortMember, TTCohortMember.cohort_id == TTCohort.id).where(
        TTCohort.org_id == ctx.org_id, TTCohortMember.org_id == ctx.org_id,
        TTCohortMember.user_id == ctx.user.id, TTCohortMember.status == 'active',
    ))).scalars().all()
    result = []
    for cohort in cohorts:
        members = (await db.execute(select(User).join(TTCohortMember, TTCohortMember.user_id == User.id).where(
            TTCohortMember.cohort_id == cohort.id, TTCohortMember.org_id == ctx.org_id,
            TTCohortMember.status == 'active',
        ))).scalars().all()
        result.append({'id': cohort.cohort_uuid, 'title': cohort.title, 'timezone': cohort.timezone,
            'schedule': cohort.schedule, 'language': cohort.language,
            'members': [{'id': u.user_uuid, 'name': u.first_name or u.username} for u in members]})
    return result


@router.put('/content/resources')
async def attach_resource(body: ResourceInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, 'content')
    program = (await db.execute(select(TTProgram).where(
        TTProgram.public_id == body.program_id, TTProgram.org_id == ctx.org_id,
    ))).scalars().first()
    if not program:
        raise HTTPException(404, 'Program not found')
    from src.security.rbac.config import get_resource_config
    config = get_resource_config(body.resource_uuid)
    if not config or config.resource_type not in {'courses', 'media'}:
        raise HTTPException(422, 'Attach a course or library media resource')
    if config.resource_type == 'courses':
        from src.db.courses.courses import Course
        model = Course
    else:
        from src.db.media.media import Media
        model = Media
    # ResourceConfig provides its UUID attribute explicitly.
    resource = (await db.execute(select(model).where(
        getattr(model, config.uuid_field) == body.resource_uuid,
    ))).scalars().first()
    if resource is None or resource.org_id != ctx.org_id:
        raise HTTPException(404, 'Resource not found')
    mapping = (await db.execute(select(TTResourceGrant).where(
        TTResourceGrant.org_id == ctx.org_id, TTResourceGrant.resource_uuid == body.resource_uuid,
    ))).scalars().first()
    if mapping:
        mapping.program_id = program.id
    else:
        db.add(TTResourceGrant(org_id=ctx.org_id, program_id=program.id, resource_uuid=body.resource_uuid))
    await db.commit()
    return {'resource_uuid': body.resource_uuid, 'program_id': program.public_id}
