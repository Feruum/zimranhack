"""Authenticated ThinkTogether learning API; mounted at /api/v1/tt."""
from typing import Any, Literal, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.thinktogether.learning import TTProgram, TTItem, TTInstrument, TTLesson, TTMockForm, TTMockRun, TTAttempt, TTSupportCase, TTDelivery, TTSkill
from src.services.thinktogether.common import TTContext, get_context, require_permission
from src.services.thinktogether import learning as service
from src.services.thinktogether import learning_curriculum as curriculum

router = APIRouter(tags=['ThinkTogether learning'])


class ProfileInput(BaseModel):
    goal: str = Field(default='', max_length=2000)
    exam_date: str = ''
    minutes_per_day: int = Field(default=30, ge=5, le=240)
    timezone: str = Field(default='UTC', max_length=100)
    language: str = Field(default='en', max_length=20)
    schedule_slots: list[dict] = Field(default_factory=list, max_length=30)
    program_id: Optional[str] = None


class ProgramInput(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    description: str = Field(default='', max_length=20000)
    is_sample: bool = True
    full_curriculum: bool = False
    blueprint_version: str = ''
    curriculum: dict = Field(default_factory=dict)
    previous_version_id: Optional[str] = None


class SkillInput(BaseModel):
    program_id: str
    code: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=400)
    description: str = Field(default='', max_length=20000)
    domain: str = ''
    prerequisites: list[str] = Field(default_factory=list, max_length=100)
    estimated_minutes: int = Field(default=5, ge=1, le=120)
    position: int = 0


class ItemInput(BaseModel):
    program_id: str
    skill_id: str
    family_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=400)
    prompt: str = Field(min_length=1, max_length=30000)
    kind: Literal['single_choice', 'multiple_choice', 'matching', 'numeric', 'reasoning'] = 'single_choice'
    choices: list[dict] = Field(default_factory=list, max_length=30)
    answer_key: dict = Field(default_factory=dict)
    rubric: dict = Field(default_factory=dict)
    explanation: str = Field(default='', max_length=30000)
    hints: list[str] = Field(default_factory=list, max_length=10)
    estimated_minutes: int = Field(default=5, ge=1, le=120)
    difficulty: int = Field(default=2, ge=1, le=5)
    is_sample: bool = True
    previous_version_id: Optional[str] = None


class InstrumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    kind: Literal['domain', 'cognitive'] = 'cognitive'
    license_reference: str = Field(default='', max_length=2000)
    consent_version: str = '1'
    consent_text: str = Field(default='', max_length=30000)
    definition: dict
    is_sample: bool = True
    previous_version_id: Optional[str] = None


class LessonInput(BaseModel):
    program_id: str
    skill_id: str
    title: str = Field(min_length=1, max_length=400)
    body_markdown: str = Field(min_length=1, max_length=150000)
    objectives: list[str] = Field(default_factory=list)
    case_families: list[str] = Field(default_factory=list)
    estimated_minutes: int = Field(default=10, ge=1, le=240)
    position: int = 0
    is_sample: bool = True
    previous_version_id: Optional[str] = None


class TransitionInput(BaseModel):
    status: Literal['draft', 'review', 'approved', 'published', 'retired']
    evidence: str = Field(default='', max_length=6000)


class ConsentInput(BaseModel):
    instrument_id: str
    version: str
    accepted: bool


class DiagnosticInput(BaseModel):
    kind: Literal['domain', 'cognitive']
    instrument_id: Optional[str] = None


class PlanInput(BaseModel):
    budget_minutes: Optional[int] = Field(default=None, ge=5, le=240)


class AttemptInput(BaseModel):
    item_id: str
    delivery_id: str
    response: dict
    idempotency_key: str = Field(min_length=1, max_length=128)
    elapsed_seconds: Optional[int] = Field(default=None, ge=0, le=86400)


class MockAnswerInput(BaseModel):
    item_id: str
    response: dict
    idempotency_key: str = Field(min_length=1, max_length=128)


class EvaluationInput(BaseModel):
    score: float = Field(ge=0, le=1)
    feedback: str = Field(min_length=1, max_length=6000)
    dimensions: dict[str, float] = Field(default_factory=dict)


async def active_access(db, ctx, program_id=None):
    if not ctx.can('content'):
        from src.services.thinktogether.billing import require_active_access
        profile = await service.profile_row(db, ctx.org_id, ctx.user.id)
        if isinstance(program_id, str):
            program_id = (await service.find(db, TTProgram, ctx.org_id, program_id)).id
        await require_active_access(db, ctx.org_id, ctx.user.id, program_id or (profile.program_id if profile else None))
        readiness = await service.learning_readiness(db, ctx.org_id, ctx.user.id)
        if not readiness['ready']:
            raise HTTPException(409, {'message': 'Complete the required diagnostics first', 'readiness': readiness})


@router.get('/programs')
async def programs(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.list_programs(db, ctx)


@router.get('/programs/{program_id}/lessons')
async def lessons(program_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await active_access(db, ctx, program_id)
    return await curriculum.lessons(db, ctx, program_id)


@router.get('/programs/{program_id}/lessons/{lesson_id}')
async def lesson(program_id: str, lesson_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await active_access(db, ctx, program_id)
    return await curriculum.lessons(db, ctx, program_id, lesson_id)


@router.get('/content/items/{item_id}')
async def item(item_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    target = await service.find(db, TTItem, ctx.org_id, item_id)
    await active_access(db, ctx, target.program_id)
    return await service.get_item(db, ctx, item_id, author=ctx.can('content'))


@router.post('/lessons/{lesson_id}/complete')
async def lesson_complete(lesson_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    target = await service.find(db, TTLesson, ctx.org_id, lesson_id)
    await active_access(db, ctx, target.program_id)
    return await curriculum.complete_lesson(db, ctx, lesson_id)


@router.post('/lessons/{lesson_id}/exercises/{item_id}')
async def lesson_exercise(lesson_id: str, item_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    lesson = await service.find(db, TTLesson, ctx.org_id, lesson_id)
    await active_access(db, ctx, lesson.program_id)
    if (lesson.status != 'published' or lesson.is_sample) and not ctx.can('content'):
        raise HTTPException(404, 'Lesson not available')
    item = await service.find(db, TTItem, ctx.org_id, item_id)
    if item.program_id != lesson.program_id or '{{item:'+item.public_id+'}}' not in lesson.body_markdown:
        raise HTTPException(404, 'Exercise not found in this lesson')
    if (item.status != 'published' or item.is_sample) and not ctx.can('content'):
        raise HTTPException(404, 'Exercise not available')
    await service.record_exposure(db, ctx.org_id, ctx.user.id, item.family_id, 'lesson', lesson.public_id)
    result = await service.issue_item(db, ctx, item, phase='coached')
    await db.commit()
    return result


@router.get('/content')
async def content(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, 'content')
    return {'programs': await service.list_programs(db, ctx),
        'skills': [service.public(i, staff=True) for i in await service.rows(db, TTSkill, TTSkill.org_id == ctx.org_id)],
        'items': [service.public(i, staff=True) for i in await service.rows(db, TTItem, TTItem.org_id == ctx.org_id)],
        'lessons': [service.public(i, staff=True) for i in await service.rows(db, TTLesson, TTLesson.org_id == ctx.org_id)],
        'mock_forms': [service.public(i, staff=True) for i in await service.rows(db, TTMockForm, TTMockForm.org_id == ctx.org_id)]}


@router.get('/content/instruments')
async def instruments(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, 'psychometrics')
    return [service.public(i, staff=True) for i in await service.rows(db, TTInstrument, TTInstrument.org_id == ctx.org_id)]


@router.post('/content/sample')
async def sample(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.seed_sample(db, ctx)


@router.post('/content/programs')
async def program_create(body: ProgramInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.create_program(db, ctx, body.model_dump(exclude_none=True))


@router.post('/content/skills')
async def skill_create(body: SkillInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.create_skill(db, ctx, body.model_dump())


@router.post('/content/items')
async def item_create(body: ItemInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.create_item(db, ctx, body.model_dump(exclude_none=True))


@router.post('/content/instruments')
async def instrument_create(body: InstrumentInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.create_instrument(db, ctx, body.model_dump(exclude_none=True))


@router.post('/content/lessons')
async def lesson_create(body: LessonInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await curriculum.create_lesson(db, ctx, body.model_dump(exclude_none=True))


@router.post('/content/{resource}/{identifier}/transition')
async def transition(resource: str, identifier: str, body: TransitionInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.transition_content(db, ctx, resource, identifier, body.status, body.evidence)


@router.post('/content/packages/validate')
async def package_validate(body: dict = Body(...), ctx: TTContext = Depends(get_context)):
    require_permission(ctx, 'content')
    return curriculum.validate_package(body)


@router.post('/content/packages/import')
async def package_import(body: dict = Body(...), ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await curriculum.import_package(db, ctx, body)


@router.get('/content/programs/{program_id}/export')
async def package_export(program_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await curriculum.export_package(db, ctx, program_id)


@router.get('/me/profile')
async def profile(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.get_profile(db, ctx)


@router.put('/me/profile')
async def profile_save(body: ProfileInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.save_profile(db, ctx, body.model_dump(exclude_none=True))


@router.post('/me/consents')
async def consent(body: ConsentInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.give_consent(db, ctx, body.instrument_id, body.accepted, body.version)


@router.get('/me/diagnostics')
async def diagnostics(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.diagnostic_options(db, ctx)


@router.post('/me/diagnostics')
async def diagnostic_start(body: DiagnosticInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.start_diagnostic(db, ctx, body.kind, body.instrument_id)


@router.get('/me/diagnostics/{run_id}')
async def diagnostic_resume(run_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.diagnostic_next(db, ctx, run_id)


@router.get('/today')
async def today(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.today(db, ctx)


@router.post('/plan')
async def plan(body: PlanInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.build_plan(db, ctx, body.budget_minutes)


@router.get('/skills')
async def skills(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.skill_map(db, ctx)


@router.get('/practice/next')
async def practice(skill_id: Optional[str] = None, review_id: Optional[str] = None, mode: Literal['case', 'retell', 'think_first'] = 'case', ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await active_access(db, ctx)
    return await service.practice_next(db, ctx, skill_id, review_id, mode)


@router.post('/practice/{delivery_id}/hint')
async def hint(delivery_id: str, hint_index: int = Query(default=0, ge=0, le=9), ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    delivery = await service.find(db, TTDelivery, ctx.org_id, delivery_id)
    if delivery.user_id != ctx.user.id:
        raise HTTPException(404, 'Delivery not found')
    if service.as_utc(delivery.expires_at) <= service.utcnow():
        raise HTTPException(409, 'Delivery has expired')
    target = await db.get(TTItem, delivery.item_id)
    await active_access(db, ctx, target.program_id)
    return await service.hint(db, ctx, delivery_id, hint_index)


@router.post('/attempts')
async def attempt(body: AttemptInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    delivery = await service.find(db, TTDelivery, ctx.org_id, body.delivery_id)
    if delivery.user_id != ctx.user.id:
        raise HTTPException(404, 'Delivery not found')
    if delivery.phase != 'diagnostic':
        target = await service.find(db, TTItem, ctx.org_id, body.item_id)
        await active_access(db, ctx, target.program_id)
    return await service.submit_attempt(db, ctx, body.item_id, body.response, body.idempotency_key,
                                         delivery_id=body.delivery_id, elapsed_seconds=body.elapsed_seconds)


@router.get('/attempts/{attempt_id}')
async def attempt_detail(attempt_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    result = await service.attempt_detail(db, ctx, attempt_id)
    attempt = await service.find(db, TTAttempt, ctx.org_id, attempt_id)
    if not attempt.diagnostic_id and not ctx.can('content'):
        from src.services.thinktogether.billing import has_active_access
        target = await db.get(TTItem, attempt.item_id)
        if not await has_active_access(db, ctx.org_id, ctx.user.id, target.program_id):
            result.pop('answer_key', None)
            result.pop('explanation', None)
            result['evaluation'] = None
    return result


@router.post('/attempts/{attempt_id}/evaluations')
async def evaluation(attempt_id: str, body: EvaluationInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.expert_evaluate(db, ctx, attempt_id, body.score, body.feedback, body.dimensions)


@router.get('/reviews')
async def reviews(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.list_reviews(db, ctx)


@router.post('/reviews/{review_id}/attempts')
async def review_start(review_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await active_access(db, ctx)
    return await service.practice_next(db, ctx, review_id=review_id)


@router.get('/programs/{program_id}/mock-forms')
async def mock_forms(program_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await curriculum.mock_forms(db, ctx, program_id)


@router.post('/mock-forms/{form_id}/runs')
async def mock_start(form_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    form = await service.find(db, TTMockForm, ctx.org_id, form_id)
    await active_access(db, ctx, form.program_id)
    return await curriculum.start_mock(db, ctx, form_id)


@router.get('/mock-runs/{run_id}')
async def mock_resume(run_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await mock_access(db, ctx, run_id)
    return await curriculum.mock_detail(db, ctx, run_id)


@router.post('/mock-runs/{run_id}/answers')
async def mock_answer(run_id: str, body: MockAnswerInput, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await mock_access(db, ctx, run_id)
    return await curriculum.answer_mock(db, ctx, run_id, body.item_id, body.response, body.idempotency_key)


@router.post('/mock-runs/{run_id}/finalize')
async def mock_finalize(run_id: str, ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    await mock_access(db, ctx, run_id)
    return await curriculum.finalize_mock(db, ctx, run_id)


async def mock_access(db, ctx, run_id):
    run = await service.find(db, TTMockRun, ctx.org_id, run_id)
    if run.user_id != ctx.user.id:
        raise HTTPException(404, 'Mock run not found')
    form = await db.get(TTMockForm, run.form_id)
    await active_access(db, ctx, form.program_id)


@router.get('/learning/readiness')
async def readiness(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    return await service.launch_readiness(db, ctx.org_id)


@router.get('/staff/review-queue')
async def review_queue(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    if not ctx.can('content') and not ctx.can('psychometrics'):
        raise HTTPException(403, 'An authorized assessment reviewer is required')
    attempts = await service.rows(db, TTAttempt, TTAttempt.org_id == ctx.org_id)
    result = []
    for attempt in attempts:
        evaluation = await service.latest_evaluation(db, attempt.id)
        if evaluation and evaluation.status == 'needs_review':
            try:
                detail = await service.attempt_detail(db, ctx, attempt.public_id)
                target = await db.get(TTItem, attempt.item_id)
                detail['item'] = {'id':target.public_id, 'title':target.title, 'prompt':target.prompt,
                                  'kind':target.kind, 'rubric':target.rubric, 'answer_key':target.answer_key}
                result.append(detail)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
    return result


@router.get('/staff/item-shortages')
async def shortages(ctx: TTContext = Depends(get_context), db: AsyncSession = Depends(get_db_session)):
    require_permission(ctx, 'support')
    return [service.public(c) for c in await service.rows(db, TTSupportCase, TTSupportCase.org_id == ctx.org_id)]
