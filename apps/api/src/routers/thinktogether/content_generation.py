"""Staff-only AI drafts and resumable progress, independent of browser lifetime."""
import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.services.thinktogether.common import TTContext, get_context, require_permission
from src.services.thinktogether import content_generation as service

router = APIRouter(tags=['ThinkTogether AI authoring'])


@router.get('/content/materials')
async def materials(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    from sqlmodel import select
    from src.db.thinktogether.jobs import TTJob
    from src.services.thinktogether.course_materials import KIND, public_material
    require_permission(ctx, 'content')
    rows = (await db.exec(select(TTJob).where(TTJob.org_id == ctx.org_id, TTJob.kind == KIND,
        TTJob.status == 'done').order_by(TTJob.id.desc()).limit(30))).all()
    return [public_material(row) for row in rows]


@router.post('/content/materials', status_code=201)
async def upload_material(request: Request, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    from starlette.datastructures import UploadFile
    from starlette.formparsers import MultiPartException
    from src.services.thinktogether.course_materials import MAX_BYTES, extract_material, save_material
    require_permission(ctx, 'content')
    if not request.headers.get('content-type', '').lower().startswith('multipart/form-data'):
        raise HTTPException(415, 'Upload a file using multipart/form-data.')
    try:
        if int(request.headers.get('content-length', '0')) > MAX_BYTES+65536:
            raise HTTPException(413, 'Choose a file smaller than 10 MB.')
    except ValueError:
        raise HTTPException(400, 'Invalid upload length.') from None
    received = 0
    async def receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get('body', b''))
        if received > MAX_BYTES+65536:
            # Starlette closes its temporary spool on this parser exception.
            raise MultiPartException('Course material upload exceeds the size limit.')
        return message
    limited = Request(request.scope, receive=receive)
    try:
        async with limited.form(max_files=1, max_fields=0, max_part_size=MAX_BYTES) as form:
            file = form.get('file')
            if not isinstance(file, UploadFile):
                raise HTTPException(422, 'Attach one file in the file field.')
            data = await file.read(MAX_BYTES+1)
            extracted = await asyncio.wait_for(asyncio.to_thread(extract_material, data, file.filename or ''), timeout=30)
            return await save_material(db, ctx, file.filename or 'material.txt', data, extracted)
    except HTTPException:
        raise
    except (ValueError, UnicodeError, TimeoutError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and type(exc) is ValueError else 'The material could not be read. Use a supported text document.'
        raise HTTPException(422, message) from None
    except Exception:
        raise HTTPException(422, 'The file could not be read. Use an unencrypted document with selectable text.') from None


@router.get('/content/generations/{identifier}/preview')
async def preview(identifier: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    job = await service.get_run(db, ctx, identifier)
    return {'title': job.payload['request']['title'], 'chapters': job.payload.get('chapters', [])}


class ReviewInput(BaseModel):
    evidence: str = Field(min_length=20, max_length=6000)


@router.get('/content/generations')
async def generations(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.list_runs(db, ctx)


@router.post('/content/generations', status_code=202)
async def create(body: service.GenerationInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.start(db, ctx, body)


@router.get('/content/generations/{identifier}')
async def status(identifier: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return service.snapshot(await service.get_run(db, ctx, identifier))


@router.post('/content/generations/{identifier}/{action}')
async def control(identifier: str, action: Literal['cancel', 'retry'], ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.control(db, ctx, identifier, action)


@router.post('/content/generations/{identifier}/review/{action}')
async def review(identifier: str, action: Literal['approve', 'publish', 'request_changes'], body: ReviewInput,
                 ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.package_action(db, ctx, identifier, action, body.evidence)


@router.get('/content/generations/{identifier}/events')
async def events(identifier: str, request: Request, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await service.get_run(db, ctx, identifier)
    await db.rollback()

    async def stream():
        signature = None
        while not await request.is_disconnected():
            try:
                current = await get_context(ctx.user, db)
                data = service.snapshot(await service.get_run(db, current, identifier))
                await db.rollback()  # Fresh committed checkpoint on the next poll.
            except HTTPException:
                yield 'event: access_revoked\ndata: {}\n\n'
                break
            key = (data['revision'], data['status'], data['attempts'], data['error_type'])
            if key != signature:
                yield f"id: {data['revision']}\nevent: progress\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                signature = key
            else:
                yield ': heartbeat\n\n'
            if data['status'] in {'done', 'dead'}:
                break
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})


@router.patch('/content/drafts/{resource}/{identifier}')
async def edit(resource: Literal['programs', 'items', 'lessons'], identifier: str, body: dict = Body(...),
               ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    from src.db.thinktogether.learning import TTProgram, TTItem, TTLesson, TTContentAudit
    from src.routers.thinktogether.learning import ProgramInput, ItemInput, LessonInput
    from src.services.thinktogether.learning_content import find, public, validate_item
    require_permission(ctx, 'content')
    model, schema, allowed = {
        'programs': (TTProgram, ProgramInput, {'title', 'description'}),
        'items': (TTItem, ItemInput, {'title', 'prompt', 'kind', 'choices', 'answer_key', 'rubric', 'explanation', 'hints', 'estimated_minutes', 'difficulty'}),
        'lessons': (TTLesson, LessonInput, {'title', 'body_markdown', 'objectives', 'estimated_minutes'}),
    }[resource]
    if not body or not set(body) <= allowed:
        raise HTTPException(422, 'Only editable draft fields may be changed.')
    row = await find(db, model, ctx.org_id, identifier)
    program = row if resource == 'programs' else await db.get(TTProgram, row.program_id, with_for_update=True)
    await db.refresh(row, with_for_update=True)
    if not program or program.org_id != ctx.org_id or row.status != 'draft' or program.status != 'draft':
        raise HTTPException(409, 'Only unapproved drafts in a draft program can be edited.')
    if program.curriculum.get('ai_generation', {}).get('state') == 'generating':
        raise HTTPException(409, 'Finish or stop generation before editing saved drafts.')
    values = row.model_dump()
    values.update(body)
    if resource != 'programs':
        values['program_id'], values['skill_id'] = str(row.program_id), str(row.skill_id)
    try:
        validated = schema.model_validate(values).model_dump()
    except ValidationError:
        raise HTTPException(422, 'Invalid draft field values.') from None
    if resource == 'items':
        validate_item(validated)
    for key in body:
        setattr(row, key, validated[key])
    db.add(row)
    db.add(TTContentAudit(org_id=ctx.org_id, actor_id=ctx.user.id, resource=resource,
        resource_id=row.public_id, previous_status='draft', status='draft', evidence='Expert edited draft fields: '+', '.join(sorted(body))))
    await db.commit()
    return public(row, staff=True)
