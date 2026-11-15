from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.thinktogether.jobs import TTJob
from src.services.thinktogether.common import TTContext, get_context, require_permission
from src.services.thinktogether.course_generation import generator

router = APIRouter(prefix='/content/course-pipeline', tags=['ThinkTogether document courses'])


@router.post('', status_code=202)
async def create(body: generator.CourseInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await generator.start(db, ctx, body)


@router.get('')
async def list_runs(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, 'content')
    jobs = (await db.exec(select(TTJob).where(TTJob.org_id == ctx.org_id, TTJob.kind == generator.KIND)
        .order_by(TTJob.id.desc()).limit(15))).all()
    return [generator.snapshot(job) for job in jobs]


@router.get('/{identifier}')
async def status(identifier: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return generator.snapshot(await generator.get_run(db, ctx, identifier))


@router.get('/{identifier}/artifacts/{name:path}')
async def artifact(identifier: str, name: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    job = await generator.get_run(db, ctx, identifier)
    content = job.payload.get('artifacts', {}).get(name) if job.status == 'done' else None
    if name == 'course.draft.md':
        content = job.payload.get('draft')
    if content is None:
        raise HTTPException(404, 'Артефакт ещё не готов или не существует.')
    if name == 'course.final.md':
        content = generator.snapshot(job)['markdown']
    media = 'image/svg+xml' if name.endswith('.svg') else 'text/csv' if name.endswith('.csv') else 'text/markdown'
    return Response(content=content, media_type=media, headers={'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; style-src 'none'; sandbox"})
