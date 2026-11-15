"""Versioned authoring, publication gates and safe delivery of content."""
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from src.db.thinktogether.learning import (
    TTProgram, TTSkill, TTItem, TTInstrument, TTContentAudit, TTDelivery,
    TTExposure, TTAttempt, TTLesson, TTMockForm,
)
from src.services.thinktogether.common import require_permission, utcnow


async def rows(db, model, *conditions):
    return (await db.execute(select(model).where(*conditions))).scalars().all()


async def find(db, model, org_id, identifier, lock=False):
    query = select(model).where(model.org_id == org_id, model.public_id == str(identifier))
    if lock:
        query = query.with_for_update()
    row = (await db.execute(query)).scalars().first()
    if row is None:
        raise HTTPException(404, "Record not found")
    return row


def public(row, *, staff=False):
    result = row.model_dump(mode="json")
    result['id'] = result.pop('public_id')
    if staff:
        result['internal_id'] = row.id
    result.pop('org_id', None)
    return result


def safe_item(item):
    # Whitelist: answer keys, grading rubrics, hints and author-only metadata
    # must never leak through a nested model serialization.
    from src.services.thinktogether.learning_mechanics import safe_practice_metadata
    return {'id': item.public_id, 'title': item.title, 'prompt': item.prompt,
            'kind': item.kind, 'choices': [dict({'id': c.get('id'), 'text': c.get('text', '')},
                **({'side': c['side']} if item.kind == 'matching' and c.get('side') in {'left', 'right'} else {})) for c in item.choices],
            'estimated_minutes': item.estimated_minutes, 'difficulty': item.difficulty,
            'is_sample': item.is_sample, 'version': item.version, **safe_practice_metadata(item)}


async def list_programs(db, ctx):
    query = [TTProgram.org_id == ctx.org_id]
    if not ctx.can('content'):
        query.extend([TTProgram.status == 'published', TTProgram.is_sample == False])
    return [public(p, staff=ctx.can('content')) for p in await rows(db, TTProgram, *query)]


async def get_item(db, ctx, item_id, *, author=False):
    item = await find(db, TTItem, ctx.org_id, item_id)
    if author:
        require_permission(ctx, 'content')
        return public(item, staff=True)
    if item.status != 'published' or item.is_sample:
        if not ctx.can('content'):
            raise HTTPException(404, 'Item not available')
    if item.rubric.get('practice_mode', 'case') != 'case':
        await record_exposure(db, ctx.org_id, ctx.user.id, item.family_id, 'coached', item.public_id)
        await db.commit()
    return safe_item(item)


async def create_program(db, ctx, data):
    require_permission(ctx, 'content')
    allowed = {'title', 'description', 'is_sample', 'full_curriculum', 'blueprint_version', 'curriculum'}
    values = {k: v for k, v in data.items() if k in allowed}
    if not values.get('title', '').strip():
        raise HTTPException(422, 'Title is required')
    previous = data.get('previous_version_id')
    if previous:
        prior = await find(db, TTProgram, ctx.org_id, previous, lock=True)
        versions = await rows(db, TTProgram, TTProgram.org_id == ctx.org_id, TTProgram.series_id == prior.series_id)
        values.update(series_id=prior.series_id, version=max(p.version for p in versions)+1)
    program = TTProgram(org_id=ctx.org_id, **values)
    db.add(program)
    await db.commit()
    return public(program, staff=True)


async def create_skill(db, ctx, data):
    require_permission(ctx, 'content')
    program = await find(db, TTProgram, ctx.org_id, data['program_id'])
    if program.status != 'draft':
        raise HTTPException(409, 'Create a draft program version before editing its skill graph')
    parents = [await find(db, TTSkill, ctx.org_id, p) for p in data.get('prerequisites', [])]
    if any(p.program_id != program.id for p in parents):
        raise HTTPException(422, 'Prerequisites must belong to the same program version')
    if not data.get('code', '').strip() or not data.get('title', '').strip():
        raise HTTPException(422, 'Skill code and title are required')
    skill = TTSkill(org_id=ctx.org_id, program_id=program.id, code=data['code'], title=data['title'],
                    description=data.get('description', ''), domain=data.get('domain', ''),
                    prerequisites=[p.id for p in parents], position=data.get('position', 0),
                    estimated_minutes=max(1, min(int(data.get('estimated_minutes', 5)), 120)))
    db.add(skill)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, 'Skill code already exists in this version')
    return public(skill, staff=True)


def validate_item(data):
    from src.services.thinktogether.learning_mechanics import validate_practice_metadata
    validate_practice_metadata(data)
    if data.get('kind', 'single_choice') not in {'single_choice', 'multiple_choice', 'matching', 'numeric', 'reasoning'}:
        raise HTTPException(422, 'Unsupported item kind')
    if not data.get('title', '').strip() or not data.get('prompt', '').strip() or not data.get('family_id', '').strip():
        raise HTTPException(422, 'Title, prompt and stable family ID are required')
    if not 1 <= int(data.get('estimated_minutes', 5)) <= 120:
        raise HTTPException(422, 'Estimated minutes must be between 1 and 120')
    kind = data.get('kind', 'single_choice')
    key, choices = data.get('answer_key', {}), data.get('choices', [])
    if not isinstance(key, dict) or not isinstance(choices, list) or any(not isinstance(c, dict) or not isinstance(c.get('id'), str) or not isinstance(c.get('text'), str) for c in choices):
        raise HTTPException(422, 'Choices require text and string IDs; answer key must be an object')
    ids = [c.get('id') for c in choices]
    if kind in {'single_choice', 'multiple_choice'}:
        if len(ids) < 2 or len(set(ids)) != len(ids) or any(not i for i in ids):
            raise HTTPException(422, 'Choice IDs must be unique and at least two are required')
        correct = [key.get('choice')] if kind == 'single_choice' else key.get('choices', [])
        if not correct or any(c not in ids for c in correct):
            raise HTTPException(422, 'Answer key must reference existing choices')
    if kind == 'matching':
        left = {c['id'] for c in choices if c.get('side') == 'left'}
        right = {c['id'] for c in choices if c.get('side') == 'right'}
        matches = key.get('matches', {})
        if not left or not right or len(set(ids)) != len(ids) or len(left)+len(right) != len(choices):
            raise HTTPException(422, 'Matching choices need distinct IDs and left or right sides')
        if not isinstance(matches, dict) or set(matches) != left or any(not isinstance(v, str) or v not in right for v in matches.values()):
            raise HTTPException(422, 'The matching key must map every left option to an available right option')
    if kind == 'numeric':
        try:
            float(key['value'])
            if float(key.get('tolerance', 0)) < 0:
                raise ValueError()
        except (TypeError, KeyError, ValueError):
            raise HTTPException(422, 'Numeric key needs value and non-negative tolerance')
    if kind == 'reasoning' and (not isinstance(data.get('rubric'), dict) or not data['rubric'].get('dimensions')):
        raise HTTPException(422, 'Reasoning items require expert-authored rubric dimensions')


async def create_item(db, ctx, data):
    require_permission(ctx, 'content')
    validate_item(data)
    program = await find(db, TTProgram, ctx.org_id, data['program_id'])
    skill = await find(db, TTSkill, ctx.org_id, data['skill_id'])
    if program.status == 'published':
        raise HTTPException(409, 'Published program content is frozen; create a new program version')
    if skill.program_id != program.id:
        raise HTTPException(422, 'Skill does not belong to program')
    allowed = {'family_id', 'title', 'prompt', 'kind', 'choices', 'answer_key', 'rubric',
               'explanation', 'hints', 'estimated_minutes', 'difficulty', 'is_sample'}
    values = {k: v for k, v in data.items() if k in allowed}
    if data.get('previous_version_id'):
        prior = await find(db, TTItem, ctx.org_id, data['previous_version_id'], lock=True)
        if prior.family_id != data['family_id']:
            raise HTTPException(422, 'A version must preserve the case family')
        versions = await rows(db, TTItem, TTItem.org_id == ctx.org_id, TTItem.series_id == prior.series_id)
        values.update(series_id=prior.series_id, version=max(i.version for i in versions)+1)
    item = TTItem(org_id=ctx.org_id, program_id=program.id, skill_id=skill.id, **values)
    db.add(item)
    await db.commit()
    return public(item, staff=True)


async def create_instrument(db, ctx, data):
    require_permission(ctx, 'psychometrics')
    if data.get('kind', 'cognitive') not in {'domain', 'cognitive'}:
        raise HTTPException(422, 'Instrument kind must be domain or cognitive')
    allowed = {'title', 'kind', 'is_sample', 'license_reference', 'consent_version', 'consent_text', 'definition'}
    values = {k: v for k, v in data.items() if k in allowed}
    definition = dict(values.get('definition', {}))
    items = [await find(db, TTItem, ctx.org_id, i) for i in definition.get('item_ids', [])]
    if any(i.rubric.get('practice_mode', 'case') != 'case' for i in items):
        raise HTTPException(422, 'Diagnostic instruments require independent assessment items')
    if len({i.id for i in items}) != len(items):
        raise HTTPException(422, 'Instrument items must be unique')
    mapping = {i.public_id: i.id for i in items}
    dimensions = definition.get('dimensions')
    if dimensions is not None:
        if not isinstance(dimensions, dict) or not dimensions:
            raise HTTPException(422, 'Dimensions must map names to instrument item IDs')
        converted = {}
        for name, ids in dimensions.items():
            if not isinstance(ids, list) or not ids or any(str(i) not in mapping for i in ids):
                raise HTTPException(422, 'Dimension items must reference items in this instrument')
            converted[str(name)] = [mapping[str(i)] for i in ids]
        if {i for ids in converted.values() for i in ids} != {i.id for i in items}:
            raise HTTPException(422, 'Every instrument item must contribute to a scored dimension')
        definition['dimensions'] = converted
    definition['item_ids'] = [i.id for i in items]
    values['definition'] = definition
    if data.get('previous_version_id'):
        prior = await find(db, TTInstrument, ctx.org_id, data['previous_version_id'], lock=True)
        versions = await rows(db, TTInstrument, TTInstrument.org_id == ctx.org_id, TTInstrument.series_id == prior.series_id)
        values.update(series_id=prior.series_id, version=max(i.version for i in versions)+1)
    instrument = TTInstrument(org_id=ctx.org_id, **values)
    db.add(instrument)
    await db.commit()
    return public(instrument, staff=True)


async def validate_program_graph(db, program):
    skills = await rows(db, TTSkill, TTSkill.org_id == program.org_id, TTSkill.program_id == program.id)
    graph = {s.id: s.prerequisites for s in skills}
    done, active = set(), set()
    def visit(node):
        if node in active or node not in graph:
            raise HTTPException(422, 'Prerequisite graph contains a cycle or foreign skill')
        if node in done:
            return
        active.add(node)
        for parent in graph[node]:
            visit(parent)
        active.remove(node)
        done.add(node)
    for skill in skills:
        visit(skill.id)
    return skills


async def practice_reserved_families(db, org_id, program_id, *, proposed_instrument=None):
    """Shared content reservations for selection and publication validation.

    Draft instruments cannot remove live practice material. A proposed publish
    is checked against the exact instrument versions that will remain live.
    """
    forms = await rows(db, TTMockForm, TTMockForm.org_id == org_id, TTMockForm.program_id == program_id)
    reserved_ids = {iid for form in forms for iid in form.item_ids}
    instruments = await rows(db, TTInstrument, TTInstrument.org_id == org_id,
                             TTInstrument.status == 'published', TTInstrument.is_sample == False)
    if proposed_instrument is not None:
        if proposed_instrument.org_id != org_id:
            raise HTTPException(404, 'Instrument not found')
        # Publication retires other published versions in the same series.
        instruments = [i for i in instruments if i.series_id != proposed_instrument.series_id]
        instruments.append(proposed_instrument)
    reserved_ids.update(iid for instrument in instruments for iid in instrument.definition.get('item_ids', []))
    reserved_items = await rows(db, TTItem, TTItem.org_id == org_id, TTItem.id.in_(reserved_ids)) if reserved_ids else []
    families = {item.family_id for item in reserved_items}
    lessons = await rows(db, TTLesson, TTLesson.org_id == org_id, TTLesson.program_id == program_id,
                         TTLesson.status == 'published', TTLesson.is_sample == False)
    families.update(family for lesson in lessons for family in lesson.case_families)
    return families


async def validate_program_practice_bank(db, program, *, proposed_instrument=None):
    """Require three real independent families per skill outside reserved uses."""
    from src.services.thinktogether.learning_mechanics import practice_mode
    skills = await rows(db, TTSkill, TTSkill.org_id == program.org_id, TTSkill.program_id == program.id)
    items = await rows(db, TTItem, TTItem.org_id == program.org_id, TTItem.program_id == program.id,
                       TTItem.status == 'published', TTItem.is_sample == False)
    reserved = await practice_reserved_families(db, program.org_id, program.id, proposed_instrument=proposed_instrument)
    counts = {skill.public_id: len({i.family_id for i in items if i.skill_id == skill.id
                                   and practice_mode(i) == 'case' and i.family_id not in reserved}) for skill in skills}
    missing = {identifier: count for identifier, count in counts.items() if count < 3}
    if not skills or missing:
        raise HTTPException(409, {'message': 'The independent practice bank needs at least three distinct case families per skill outside lessons, mock exams and published diagnostics',
                                  'required_families': 3, 'available_by_skill': missing})
    return counts


async def validate_instrument_practice_banks(db, instrument):
    """Serialize publication with affected courses and preserve their live banks."""
    item_ids = instrument.definition.get('item_ids', [])
    referenced = await rows(db, TTItem, TTItem.org_id == instrument.org_id, TTItem.id.in_(item_ids))
    families = {i.family_id for i in referenced}
    # Family aliases in another program/version are also reserved by a diagnostic.
    program_ids = (await db.execute(select(TTItem.program_id).where(TTItem.org_id == instrument.org_id,
                                 TTItem.family_id.in_(families)).distinct())).scalars().all() if families else []
    for program_id in sorted(program_ids):
        program = (await db.execute(select(TTProgram).where(TTProgram.org_id == instrument.org_id,
            TTProgram.id == program_id).with_for_update().execution_options(populate_existing=True))).scalars().first()
        if program and program.status == 'published':
            await validate_program_practice_bank(db, program, proposed_instrument=instrument)


async def transition_content(db, ctx, resource, identifier, status, evidence='', *, commit=True):
    models = {'programs': TTProgram, 'items': TTItem, 'instruments': TTInstrument, 'lessons': TTLesson, 'mock_forms': TTMockForm}
    if resource not in models:
        raise HTTPException(404, 'Content type not found')
    require_permission(ctx, 'psychometrics' if resource == 'instruments' else 'content')
    row = await find(db, models[resource], ctx.org_id, identifier)
    parent = None
    if resource in {'items', 'lessons', 'mock_forms'}:
        parent = await db.get(TTProgram, row.program_id, with_for_update=True)
        await db.refresh(row, with_for_update=True)
        if parent and parent.status == 'published':
            raise HTTPException(409, 'Published program content is frozen; create a new program version')
    else:
        await db.refresh(row, with_for_update=True)
        if resource == 'programs':
            parent = row
    if parent and parent.curriculum.get('ai_generation', {}).get('state') == 'generating':
        raise HTTPException(409, 'Finish or stop AI generation before reviewing this draft.')
    transitions = {'draft': {'review'}, 'review': {'draft', 'approved'}, 'approved': {'published', 'draft'}, 'published': {'retired'}}
    if status not in transitions.get(row.status, set()):
        raise HTTPException(409, 'Invalid content workflow transition')
    if status == 'approved':
        if not evidence.strip():
            raise HTTPException(422, 'Expert approval evidence is required')
        if resource == 'programs':
            await validate_program_graph(db, row)
        if resource == 'items':
            validate_item(row.model_dump())
        row.approval_evidence, row.approved_by = evidence, ctx.user.id
    if status == 'published':
        if row.is_sample or not row.approval_evidence:
            raise HTTPException(409, 'Sample or unapproved content cannot be published')
        if resource == 'programs':
            skills = await validate_program_graph(db, row)
            demo = row.curriculum.get('kind') == 'demo_three_chapters'
            if demo:
                from src.services.thinktogether.course_demo import validate_demo
                try:
                    await validate_demo(db, ctx.org_id, row, published=True)
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from None
            elif not row.full_curriculum or not row.blueprint_version or not skills:
                raise HTTPException(409, 'A reviewed full curriculum and exam blueprint are required')
            if not demo:
                await validate_program_practice_bank(db, row)
            from src.services.thinktogether.learning_curriculum import export_package, validate_package
            package = await export_package(db, ctx, row.public_id)
            validation = {'valid': True} if demo else validate_package(package)
            if not validation['valid']:
                raise HTTPException(409, validation)
            lessons = await rows(db, TTLesson, TTLesson.org_id == ctx.org_id, TTLesson.program_id == row.id)
            forms = await rows(db, TTMockForm, TTMockForm.org_id == ctx.org_id, TTMockForm.program_id == row.id)
            if any(l.status != 'published' or l.is_sample for l in lessons) or any(f.status != 'published' or f.is_sample for f in forms):
                raise HTTPException(409, 'All required lessons and mock forms need explicit publication approval')
            row.published_at = utcnow()
        elif resource == 'instruments':
            # Serialize releases in one instrument series even when versions
            # refer to different courses; each replacement must retire the
            # actual current version before its bank reservation takes effect.
            await db.execute(select(TTInstrument).where(TTInstrument.org_id == ctx.org_id,
                TTInstrument.series_id == row.series_id).order_by(TTInstrument.id).limit(1)
                .with_for_update().execution_options(populate_existing=True))
            if row.kind == 'cognitive':
                from src.services.thinktogether.learning_support import validate_support_policy
                try:
                    validate_support_policy(row.definition, required=True)
                except (ValueError, TypeError) as exc:
                    raise HTTPException(409, str(exc))
            if not row.license_reference or not row.consent_text or not row.definition.get('item_ids'):
                raise HTTPException(409, 'Licensed instrument, informed consent and scoring definition are required')
            if row.definition.get('scoring') != 'dimension_mean':
                raise HTTPException(409, 'Unsupported instrument scoring rule')
            for iid in row.definition['item_ids']:
                item = await db.get(TTItem, iid)
                if not item or item.org_id != ctx.org_id or item.status != 'published' or item.is_sample or item.rubric.get('practice_mode', 'case') != 'case':
                    raise HTTPException(409, 'All instrument items must be approved and published')
            await validate_instrument_practice_banks(db, row)
        elif resource == 'mock_forms':
            if not row.item_ids or row.duration_minutes < 1:
                raise HTTPException(409, 'Mock form needs items and a positive duration')
            for iid in row.item_ids:
                item = await db.get(TTItem, iid)
                if not item or item.org_id != ctx.org_id or item.program_id != row.program_id or item.status != 'published' or item.is_sample or item.rubric.get('practice_mode', 'case') != 'case':
                    raise HTTPException(409, 'Mock items must be published in the same program')
        if hasattr(row, 'series_id'):
            older = await rows(db, models[resource], models[resource].org_id == ctx.org_id,
                               models[resource].series_id == row.series_id, models[resource].status == 'published')
            for previous in older:
                if previous.id != row.id and (not hasattr(row, 'program_id') or previous.program_id == row.program_id):
                    previous.status = 'retired'
                    db.add(previous)
    old = row.status
    row.status = status
    db.add(row)
    db.add(TTContentAudit(org_id=ctx.org_id, actor_id=ctx.user.id, resource=resource,
                          resource_id=row.public_id, previous_status=old, status=status, evidence=evidence))
    if commit:
        await db.commit()
    else:
        await db.flush()
    return public(row, staff=True)


async def launch_readiness(db, org_id):
    programs = await rows(db, TTProgram, TTProgram.org_id == org_id, TTProgram.status == 'published',
                          TTProgram.is_sample == False, TTProgram.full_curriculum == True)
    instruments = await rows(db, TTInstrument, TTInstrument.org_id == org_id,
                             TTInstrument.status == 'published', TTInstrument.is_sample == False)
    gates = {'approved_curriculum': any(p.approval_evidence for p in programs),
             'approved_domain_instrument': any(i.kind == 'domain' and i.approval_evidence and i.license_reference for i in instruments),
             'approved_cognitive_instrument': any(i.kind == 'cognitive' and i.approval_evidence and i.license_reference for i in instruments)}
    return {'ready': all(gates.values()), 'gates': gates,
            'blocked_reasons': [k for k, v in gates.items() if not v]}


async def issue_item(db, ctx, item, *, phase='independent', diagnostic_id=None, review_id=None):
    delivery = TTDelivery(org_id=ctx.org_id, user_id=ctx.user.id, item_id=item.id, phase=phase,
                          diagnostic_id=diagnostic_id, review_id=review_id, expires_at=utcnow()+timedelta(hours=4))
    db.add(delivery)
    await db.flush()
    return {'delivery_id': delivery.public_id, 'item': safe_item(item), 'phase': phase}


async def record_exposure(db, org_id, user_id, family_id, source, source_id=''):
    """Call inside the room/board/voice transaction before exposing case help."""
    from src.services.thinktogether.learning_evidence import learner_lock
    await learner_lock(db, org_id, user_id)
    db.add(TTExposure(org_id=org_id, user_id=user_id, family_id=family_id,
                      source=source, source_id=str(source_id)))
    await db.flush()


async def hint(db, ctx, delivery_id, hint_index=0):
    delivery = await find(db, TTDelivery, ctx.org_id, delivery_id)
    if delivery.user_id != ctx.user.id:
        raise HTTPException(404, 'Delivery not found')
    if delivery.room_id or delivery.phase in {'diagnostic', 'mock'}:
        raise HTTPException(409, 'This assessment does not permit hints')
    item = await db.get(TTItem, delivery.item_id)
    if not 0 <= hint_index < len(item.hints):
        raise HTTPException(404, 'Hint not found')
    await record_exposure(db, ctx.org_id, ctx.user.id, item.family_id, 'hint', delivery.public_id)
    await db.commit()
    return {'hint': item.hints[hint_index], 'independent_evidence': False}


async def seed_sample(db, ctx):
    require_permission(ctx, 'content')
    existing = (await rows(db, TTProgram, TTProgram.org_id == ctx.org_id, TTProgram.title == 'PMP decision practice — original draft sample'))
    if existing:
        return {'program': public(existing[0], staff=True), 'notice': 'Draft demonstration only; not a full or approved curriculum.'}
    program = TTProgram(org_id=ctx.org_id, title='PMP decision practice — original draft sample',
                        blueprint_version='PMP-2026-07',
                        description='Original demonstration scenarios for a developer walkthrough. Expert review and licensed assessment required.',
                        curriculum={'sample_only': True, 'domains': ['People', 'Process', 'Business environment'],
                                    'domain_weights': {'People': 33, 'Process': 41, 'Business environment': 26}})
    db.add(program)
    await db.flush()
    definitions = [
        ('stakeholder', 'Stakeholder collaboration', 'People', 'A new stakeholder challenges an agreed project outcome. What should the project manager do first?',
         [('a', 'Understand their concerns and assess the impact with the team'), ('b', 'Reject the request because planning is complete'), ('c', 'Commit to the change immediately')],
         'a', 'Clarifying interests and impact precedes a change decision.'),
        ('risk', 'Risk response reasoning', 'Process', 'A supplier delay is plausible but has not happened. Which next step supports a deliberate response?',
         [('a', 'Record the risk, estimate its impact and agree an owner and response'), ('b', 'Treat the risk as a confirmed issue'), ('c', 'Wait without assigning an owner')],
         'a', 'A risk is uncertainty; an owner and planned response make it actionable.'),
        ('value', 'Value alignment', 'Business environment', 'A deliverable meets its specification, but evidence suggests it no longer supports the intended outcome. What should happen next?',
         [('a', 'Review the value assumption with the sponsor before deciding changes'), ('b', 'Ignore outcome evidence because the specification was met'), ('c', 'Cancel all work without consultation')],
         'a', 'Project decisions should be checked against benefits and value.')]
    for pos, (code, title, domain, prompt, choices, correct, explanation) in enumerate(definitions):
        skill = TTSkill(org_id=ctx.org_id, program_id=program.id, code=code, title=title, domain=domain, position=pos)
        db.add(skill)
        await db.flush()
        db.add(TTItem(org_id=ctx.org_id, program_id=program.id, skill_id=skill.id,
                      family_id=f'sample-{code}-1', title=title+' sample', prompt=prompt,
                      choices=[{'id': k, 'text': v} for k, v in choices], answer_key={'choice': correct},
                      explanation=explanation, hints=['Identify the uncertainty and the next decision before acting.']))
        db.add(TTLesson(org_id=ctx.org_id, program_id=program.id, skill_id=skill.id,
            title=title+' — original sample lesson', position=pos,
            case_families=[f'sample-{code}-1'],
            objectives=['Identify the next decision', 'Justify a proportionate action with evidence'],
            body_markdown=f'# {title}\n\nThis original draft sample supports a developer walkthrough. It is not an approved complete PMP course.\n\n## Decision process\n\n1. Describe the outcome and who is affected.\n2. Separate observed facts from assumptions.\n3. Compare proportionate actions before committing.\n\n## Worked example\n\n{prompt}\n\n{explanation}\n\n## Reflection\n\nWhat new evidence would make you change the decision? Explain the trade-off, then try a fresh case independently.'))
    await db.commit()
    return {'program': public(program, staff=True), 'notice': 'Draft demonstration only; not a full or approved curriculum.'}
