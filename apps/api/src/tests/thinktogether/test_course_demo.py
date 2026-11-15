import csv
import io
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi import HTTPException, FastAPI
from httpx import AsyncClient, ASGITransport
from sqlmodel import select

from src.services.thinktogether import content_generation as generation, course_demo as demo
from src.services.thinktogether.course_blocks import CSV_HEADERS, parse_blocks, parse_exercise_csv, insert_exercises
from src.services.thinktogether.course_materials import extract_material, save_material, get_materials, select_context, validate_references
from src.tests.thinktogether.test_content_generation import ctx, step, draft_item
from src.db.thinktogether.learning import TTProgram, TTLesson, TTItem


def markdown(chapter=1):
    text = '# Original chapter\n\n:::explain\n'+('Explain the underlying principle and relevant constraints in this situation. '*7)+'\n:::\n'
    text += ':::example\nA worked example illustrates an informed decision with affected people.\n:::\n'
    text += ':::reflection\nWhich evidence would change this decision?\n:::\n'
    for n in range(1, 4):
        key = f'c{chapter}_q{n}'
        text += f':::exercise {key}\nApply the principle to a different situation.\n{{{{csv:{key}}}}}\n:::\n'
    return text


def artifact(ids, references=None):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    for key in ids:
        item = draft_item('Original scenario '+key)
        writer.writerow([key, item['kind'], item['prompt'], json.dumps(item['choices']),
                         json.dumps(item['answer_key']), item['explanation'], json.dumps(references or [])])
    return output.getvalue()


def test_markdown_csv_roundtrip_and_code_fences():
    source = markdown()
    ids = [block['id'] for block in parse_blocks(source) if block['type'] == 'exercise']
    assert ids == ['c1_q1', 'c1_q2', 'c1_q3']
    assert len(parse_exercise_csv(artifact(ids), ids)) == 3
    bindings = {key: f'00000000-0000-0000-0000-{index:012d}' for index, key in enumerate(ids, 1)}
    compiled = insert_exercises(source, bindings)
    assert '{{csv:' not in compiled and compiled.count('{{item:') == 3
    assert len([b for b in parse_blocks(compiled, compiled=True) if b['type'] == 'exercise']) == 3
    assert parse_blocks('```md\n:::not_a_directive\n```\n'+source)[0]['type'] == 'markdown'


@pytest.mark.parametrize('fence', ['```', '~~~'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_csv_compilation_preserves_fenced_literal_markers(fence, newline):
    literal = f'{fence}md\n{{{{csv:c1_q1}}}}\n{{{{csv:example_only}}}}\n{fence}\n'
    source = (literal + markdown()).replace('\n', newline)
    bindings = {f'c1_q{n}': f'00000000-0000-0000-0000-{n:012d}' for n in range(1, 4)}
    compiled = insert_exercises(source, bindings)
    assert compiled.startswith(literal.replace('\n', newline))
    assert compiled.count('{{item:') == 3
    assert len([b for b in parse_blocks(compiled, compiled=True) if b['type'] == 'exercise']) == 3


@pytest.mark.parametrize('directive', [':::exercise\tc1_q1', '::: exercise c1_q1'])
def test_compilation_uses_the_same_directive_whitespace_rules(directive):
    source = markdown().replace(':::exercise c1_q1', directive)
    bindings = {f'c1_q{n}': f'00000000-0000-0000-0000-{n:012d}' for n in range(1, 4)}
    assert len([b for b in parse_blocks(insert_exercises(source, bindings), compiled=True)
                if b['type'] == 'exercise']) == 3


def test_fenced_literals_inside_exercise_are_not_counted_as_bindings():
    literal = '```md\n{{csv:c1_q1}}\n{{item:00000000-0000-0000-0000-000000000099}}\n```\n'
    source = markdown().replace(':::exercise c1_q1\n', ':::exercise c1_q1\n' + literal)
    bindings = {f'c1_q{n}': f'00000000-0000-0000-0000-{n:012d}' for n in range(1, 4)}
    compiled = insert_exercises(source, bindings)
    assert literal in compiled
    assert len([b for b in parse_blocks(compiled, compiled=True) if b['type'] == 'exercise']) == 3


@pytest.mark.parametrize('change', [lambda text: text.replace(':::explain', ':::unknown'),
    lambda text: text.replace('{{csv:c1_q1}}', '{{csv:other}}'),
    lambda text: text.replace(':::exercise c1_q2', ':::exercise c1_q1'),
    lambda text: text.rstrip()[:-3]])
def test_bad_directives_are_rejected(change):
    with pytest.raises(ValueError):
        parse_blocks(change(markdown()))


def test_csv_rejects_incomplete_wrong_keys_and_formulas():
    valid = artifact(['c1_q1', 'c1_q2', 'c1_q3'])
    with pytest.raises(ValueError):
        parse_exercise_csv(valid, ['c1_q1'])
    with pytest.raises(ValueError):
        parse_exercise_csv(valid.replace('Original scenario', '=WEBSERVICE', 1), ['c1_q1', 'c1_q2', 'c1_q3'])
    with pytest.raises(ValueError):
        parse_exercise_csv(valid.replace('answer_key_json', 'answer'), ['c1_q1', 'c1_q2', 'c1_q3'])


def test_material_text_docx_and_reference_validation():
    text = b'Project decisions should consider evidence and stakeholder needs.'
    extracted = extract_material(text, 'notes.md')
    assert extracted['characters'] == len(text)
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Original training note.</w:t></w:r></w:p></w:body></w:document>')
    assert 'Original training note.' in extract_material(buffer.getvalue(), 'training.docx')['chunks'][0]['text']
    context = select_context([], text.decode(), 'stakeholder evidence')
    validate_references([{'id': 'input:1', 'quote': 'decisions should consider evidence'}], context, required=True)
    with pytest.raises(ValueError):
        validate_references([{'id': 'input:1', 'quote': 'This quotation was fabricated.'}], context, required=True)
    with pytest.raises(ValueError):
        extract_material(b'\x00binary', 'file.exe')


async def test_materials_private_scoped_and_deduplicated(db, org):
    data = b'Original course notes about evaluating alternatives and explaining decisions.'
    extracted = extract_material(data, 'course.txt')
    first = await save_material(db, ctx(), '../../course.txt', data, extracted)
    second = await save_material(db, ctx(), 'same.txt', data, extracted)
    assert first['id'] == second['id'] and first['name'] == 'course.txt'
    with pytest.raises(HTTPException) as hidden:
        await get_materials(db, ctx(org_id=2), [first['id']])
    assert hidden.value.status_code == 404
    with pytest.raises(HTTPException) as denied:
        await get_materials(db, ctx(False), [first['id']])
    assert denied.value.status_code == 403


async def test_real_multipart_route_extracts_and_bounds_upload(db, org):
    from src.routers.thinktogether.content_generation import router
    from src.services.thinktogether.common import get_context
    from src.core.events.database import get_db_session
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_context] = lambda: ctx()
    app.dependency_overrides[get_db_session] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/content/materials', files={'file': ('notes.txt', b'Original topic material for a generated training course.', 'text/plain')})
        assert response.status_code == 201
        assert response.json()['characters'] > 20 and 'chunks' not in response.json()
        bad = await client.post('/content/materials', files={'file': ('program.exe', b'not a material', 'application/octet-stream')})
        assert bad.status_code == 422


async def test_three_chapter_two_agent_pipeline_and_expert_publication(db, org, monkeypatch):
    monkeypatch.setattr(generation, 'configured_model', lambda: 'test-model')
    async def author(db, job): return ctx()
    monkeypatch.setattr(generation, 'worker_context', author)
    calls = []
    async def agent(job, schema, instruction, *, material_context=None):
        calls.append(schema.__name__)
        if schema is demo.DemoOutline:
            return demo.DemoOutline(description='A concise original training demo with three coherent chapters.',
                chapters=[demo.ChapterOutline(title=f'Chapter {n}', description=f'Explain and apply original principle {n} in different settings.') for n in range(1, 4)])
        if schema is demo.ChapterDraft:
            return demo.ChapterDraft(markdown=markdown(job.payload['chapter_index']+1),
                objectives=['Explain the principle.', 'Apply it to an original case.'])
        values = parse_exercise_csv(artifact(instruction['expected_ids']), instruction['expected_ids'])
        return demo.ExerciseRows(exercises=[demo.DemoExercise(**{k: v for k, v in row.items() if k != 'title'}) for row in values])
    monkeypatch.setattr(generation, 'ask', agent)
    started = await generation.start(db, ctx(), generation.GenerationInput(title='Any course topic',
        course_format='demo_three_chapters', idempotency_key='demo'))
    job = await generation.get_run(db, ctx(), started['id'])
    assert started['total_steps'] == 8
    for _ in range(8): assert await step(db, job)
    assert job.payload['stage'] == 'ready_for_review'
    assert calls.count('ChapterDraft') == 3 and calls.count('ExerciseRows') == 3
    lessons = (await db.exec(select(TTLesson))).all()
    assert len(lessons) == 3 and all('{{csv:' not in lesson.body_markdown for lesson in lessons)
    assert len((await db.exec(select(TTItem))).all()) == 9
    assert not (await db.exec(select(TTProgram))).one().full_curriculum
    from src.routers.thinktogether.learning import lesson_exercise
    first_item = (await db.exec(select(TTItem).where(TTItem.skill_id == lessons[0].skill_id))).first()
    delivered = await lesson_exercise(lessons[0].public_id, first_item.public_id, ctx(), db)
    assert delivered['phase'] == 'coached' and 'answer_key' not in delivered['item']
    with pytest.raises(HTTPException) as wrong_chapter:
        await lesson_exercise(lessons[1].public_id, first_item.public_id, ctx(), db)
    assert wrong_chapter.value.status_code == 404
    program = (await db.exec(select(TTProgram))).one()
    program.full_curriculum = True
    db.add(program); await db.commit()
    with pytest.raises(HTTPException) as false_full_claim:
        await generation.package_action(db, ctx(), job.job_uuid, 'approve', 'This three-chapter demo must not count as a full preparation course.')
    assert false_full_claim.value.status_code == 409
    program.full_curriculum = False
    db.add(program); await db.commit()
    await generation.package_action(db, ctx(), job.job_uuid, 'approve', 'Expert checked all three chapters, material references and CSV answer keys.')
    await generation.package_action(db, ctx(), job.job_uuid, 'publish', 'Expert explicitly publishes this three-chapter demo course.')
    assert (await db.exec(select(TTProgram))).one().status == 'published'
    from src.services.thinktogether.learning_content import launch_readiness
    assert not (await launch_readiness(db, 1))['gates']['approved_curriculum']


async def test_course_provider_override_does_not_mutate_global_config(monkeypatch):
    from src.services.thinktogether.authoring_provider import build_authoring_model
    from pydantic_ai.models import openai
    from pydantic_ai.providers import openrouter
    from types import SimpleNamespace
    monkeypatch.setenv('TT_CONTENT_PROVIDER', 'openrouter')
    monkeypatch.setenv('TT_CONTENT_ENABLED', 'true')
    monkeypatch.setenv('TT_CONTENT_API_KEY', 'unit-test-credential')
    monkeypatch.setenv('LEARNHOUSE_IS_AI_ENABLED', 'false')
    monkeypatch.setattr(openrouter, 'OpenRouterProvider', lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(openai, 'OpenAIChatModel', lambda name, provider, settings: SimpleNamespace(name=name, provider=provider, settings=settings))
    result = build_authoring_model('deepseek/test-model')
    assert result.name == 'deepseek/test-model' and result.provider.api_key == 'unit-test-credential'
    import os
    assert os.getenv('LEARNHOUSE_IS_AI_ENABLED') == 'false'
