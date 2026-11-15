import hashlib
import json
import re
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from src.db.organizations import Organization
from src.db.thinktogether.jobs import TTJob
from src.services.thinktogether.common import require_permission, utcnow
from src.services.thinktogether.content_generation import worker_context
from src.services.thinktogether.course_materials import get_materials
from src.services.thinktogether.jobs import JobDeferred, enqueue_job
from .prompts import COURSE_ARCHITECT, DIAGRAM_ARCHITECT, course_input, diagram_input
from .provider import complete, configured, MODEL
from .source import prepare_source
from .svg_renderer import render_package
from .validator import PipelineError, validate_course, validate_diagrams

KIND = 'content.course_pipeline'
BASE = '/api/v1/tt/content/course-pipeline'


class CourseInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(default='Занятые взрослые, изучающие тему с нуля', min_length=1, max_length=1000)
    goal: str = Field(default='Освоить ключевые практические идеи материала', min_length=1, max_length=2000)
    material_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


def artifact_url(job, name):
    return f'{BASE}/{job.job_uuid}/artifacts/{name}'


def snapshot(job):
    payload = job.payload
    status = 'error' if job.status == 'dead' or payload.get('error') else job.status
    result = {'id': job.job_uuid, 'title': payload['request']['title'], 'status': status,
        'stage': payload['stage'], 'model': MODEL, 'coverage': payload['coverage'],
        'created_at': job.created_at.isoformat(), 'diagrams': [], 'markdown': None}
    if status == 'error':
        result['error'] = payload.get('error') or {'SourceInsufficient': 'Недостаточно материала для трёх осмысленных глав.',
            'PipelineError': 'Ответ модели не соответствует протоколу курса или схем. Черновик сохранён, если первый этап завершился.',
            'ProviderError': 'Не удалось получить полный ответ DeepSeek. Проверьте серверный ключ, баланс и доступность API.',
            'HTTPException': 'Нет доступа к материалу или генерации.'}.get(job.last_error, 'Генерация не завершена. Проверьте серверную конфигурацию.')
    if job.status == 'done' and payload.get('stage') == 'complete':
        markdown = payload['artifacts']['course.final.md']
        for key, label in payload['labels'].items():
            url = artifact_url(job, f'diagrams/{key}.svg')
            markdown = markdown.replace(f'./diagrams/{key}.svg', url)
            result['diagrams'].append({'id': key, 'label': label, 'url': url})
        result['markdown'] = markdown
        result['download_url'] = artifact_url(job, 'course.final.md')
    result['draft_url'] = artifact_url(job, 'course.draft.md') if payload.get('draft') else None
    return result


async def get_run(db, ctx, identifier):
    require_permission(ctx, 'content')
    job = (await db.exec(select(TTJob).where(TTJob.org_id == ctx.org_id, TTJob.kind == KIND,
        TTJob.job_uuid == identifier))).first()
    if job is None:
        raise HTTPException(404, 'Генерация не найдена.')
    return job


async def start(db, ctx, request):
    require_permission(ctx, 'content')
    values = request.model_dump(exclude={'idempotency_key'})
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = f'course-pipeline:{ctx.user.id}:{request.idempotency_key}'
    await db.get(Organization, ctx.org_id, with_for_update=True)
    existing = (await db.exec(select(TTJob).where(TTJob.org_id == ctx.org_id, TTJob.idempotency_key == key))).first()
    if existing:
        if existing.payload['request_hash'] != digest:
            raise HTTPException(409, 'Этот ключ запроса уже использован с другими параметрами.')
        return snapshot(existing)
    if not configured():
        raise HTTPException(503, 'Задайте DEEPSEEK_API_KEY в окружении backend и worker.')
    active = (await db.exec(select(TTJob.id).where(TTJob.org_id == ctx.org_id, TTJob.kind == KIND,
        TTJob.status.in_(['pending', 'running', 'retry'])))).all()
    if len(active) >= 2:
        raise HTTPException(409, 'Уже создаются два курса. Дождитесь завершения.')
    material, = await get_materials(db, ctx, [request.material_id])
    try:
        source, coverage = prepare_source(material, request.title+' '+request.goal)
    except PipelineError as exc:
        raise HTTPException(422, str(exc)) from None
    job = await enqueue_job(db, ctx.org_id, KIND, {'request': values, 'request_hash': digest,
        'actor_id': ctx.user.id, 'source': source, 'coverage': coverage, 'stage': 'course',
        'prompt_version': 'tt-course-diagrams-v1'}, key)
    job.max_attempts = 3
    db.add(job)
    await db.commit()
    return snapshot(job)


async def _generate_step(db, job):
    await worker_context(db, job)
    payload = dict(job.payload)
    if payload['stage'] == 'course':
        request = payload['request']
        draft = await complete(COURSE_ARCHITECT, course_input(request['title'], request['audience'], request['goal'], payload['source']))
        validate_course(draft)
        allowed = set(re.findall(r'\[(S\d{3})\]', payload['source']))
        if not set(re.findall(r'\[(S\d{3})\]', draft)) <= allowed:
            raise PipelineError('Курс содержит ссылки на отсутствующие фрагменты.')
        payload.update(draft=draft, stage='diagrams')
        job.payload = payload
        db.add(job)
        return JobDeferred(delay_seconds=1)
    if payload['stage'] == 'diagrams':
        draft = payload['draft']
        csv_text = await complete(DIAGRAM_ARCHITECT, diagram_input(draft), max_tokens=12000)
        validate_diagrams(csv_text, validate_course(draft))
        payload.update(stage='render', csv=csv_text)
        job.payload = payload
        db.add(job)
        return JobDeferred(delay_seconds=1)
    if payload['stage'] == 'render':
        draft, csv_text = payload['draft'], payload['csv']
        diagrams = validate_diagrams(csv_text, validate_course(draft))
        with TemporaryDirectory(prefix='tt-course-') as directory:
            artifacts = render_package(draft, diagrams, directory)
        artifacts['diagrams.csv'] = csv_text
        payload.update(stage='complete', artifacts=artifacts, labels={key: value['label'] for key, value in diagrams.items()})
        payload.pop('source', None)
        payload.pop('csv', None)
        job.payload = payload
        db.add(job)
        return None
    raise PipelineError('Неизвестный этап генерации.')


async def generate_step(db, job):
    try:
        return await _generate_step(db, job)
    except PipelineError as exc:
        # Expected model failures finish the command with a terminal domain error;
        # lease recovery remains available for interrupted workers and local I/O.
        job.payload = {**job.payload, 'failed_stage': job.payload['stage'], 'stage': 'error', 'error': str(exc)}
        db.add(job)
        return None
