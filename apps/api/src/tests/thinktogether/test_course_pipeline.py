import csv
import io
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response

from src.services.thinktogether.course_generation.validator import (
    PipelineError, validate_course, validate_diagrams,
)
from src.services.thinktogether.course_generation.svg_renderer import render_package, assemble_course


@pytest.fixture
def draft():
    parts = ['<!-- TT:COURSE version="1" chapters="3" -->', '# Учебный курс', 'Результат обучения.']
    for n, kind in enumerate(('flowchart', 'hierarchy', 'comparison'), 1):
        chapter = f'ch{n:02}'
        parts.extend([f'<!-- TT:CHAPTER_START id="{chapter}" -->', f'## Глава {n}. Тема',
            '### Цель', 'Понять механизм и применить его к учебному примеру.',
            f'<!-- TT:DIAGRAM id="{chapter}_d01" type="{kind}" -->',
            '### Практика', 'Выполните упражнение. Контрольные вопросы и ответы.',
            f'<!-- TT:CHAPTER_END id="{chapter}" -->'])
    return '\n'.join(parts)+'\n'


@pytest.fixture
def diagram_csv():
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow('diagram_id,record_type,element_id,label,from_id,to_id,group,order'.split(','))
    for n, kind in enumerate(('flowchart', 'hierarchy', 'comparison'), 1):
        identifier = f'ch{n:02}_d01'
        writer.writerow([identifier, 'meta', '', 'Выбор "А", <Б> & результат', '', '', kind, 0])
        for j in range(1, 5):
            group = ('left' if j < 3 else 'right') if kind == 'comparison' else 'main'
            writer.writerow([identifier, 'node', f'n{j}', f'Шаг {j}: < & "текст"', '', '', group, j])
        if kind != 'comparison':
            for j in range(1, 4):
                writer.writerow([identifier, 'edge', f'e{j}', '', f'n{j}' if kind == 'flowchart' else 'n1', f'n{j+1}', '', j])
    return output.getvalue()


def test_three_svg_and_final_markdown(draft, diagram_csv, tmp_path):
    diagrams = validate_diagrams(diagram_csv, validate_course(draft))
    artifacts = render_package(draft, diagrams, tmp_path)
    final = artifacts['course.final.md']
    assert 'TT:DIAGRAM' not in final
    assert final.count('## Глава ') == 3
    assert artifacts['course.draft.md'] == draft
    for identifier in diagrams:
        path = tmp_path / 'diagrams' / f'{identifier}.svg'
        svg = path.read_text(encoding='utf-8')
        root = ET.fromstring(svg)
        assert root.tag.endswith('svg')
        assert '<script' not in svg and '<foreignObject' not in svg
        assert '&lt;' in svg and '&amp;' in svg
        assert 'Шаг 1: < & "текст"' in ''.join(root.itertext())


@pytest.mark.parametrize('mutate', [
    lambda s: s.replace('ch02_d01', 'ch01_d01'),
    lambda s: s.replace('type="flowchart"', 'type="svg"'),
    lambda s: s.replace('<!-- TT:CHAPTER_END id="ch01" -->', ''),
    lambda s: s+'\n<!-- TT:DIAGRAM id="ch04_d01" type="flowchart" -->',
    lambda s: s.replace('## Глава 2.', '## Глава 5.'),
    lambda s: s.replace('<!-- TT:COURSE', '```\n<!-- TT:COURSE'),
    lambda s: s.replace('<!-- TT:DIAGRAM id="ch01_d01" type="flowchart" -->', 'text <!-- TT:DIAGRAM id="ch01_d01" type="flowchart" -->'),
])
def test_invalid_course_is_rejected(draft, mutate):
    with pytest.raises(PipelineError):
        validate_course(mutate(draft))


@pytest.mark.parametrize('mutate', [
    lambda s: s.replace('ch02_d01', 'ch04_d01'),
    lambda s: s.replace('ch01_d01,node,n1', 'ch01_d01,meta,n1'),
    lambda s: s.replace(',n1,n2,,1', ',n1,n9,,1'),
    lambda s: s.replace(',n1,n2,,1', ',n1,n1,,1'),
    lambda s: s.replace('ch01_d01,node,n2', 'ch01_d01,node,n1'),
    lambda s: s.replace(',,,hierarchy,0', ',,,comparison,0'),
    lambda s: s+'"unterminated',
    lambda s: s.replace(',main,2', ',left,2'),
    lambda s: s.replace(',n3,n4,,3', ',n3,n2,,3'),
])
def test_invalid_csv_is_rejected(draft, diagram_csv, mutate):
    with pytest.raises(PipelineError):
        validate_diagrams(mutate(diagram_csv), validate_course(draft))


def test_missing_svg_does_not_publish(draft, diagram_csv, tmp_path):
    diagrams = validate_diagrams(diagram_csv, validate_course(draft))
    render_package(draft, diagrams, tmp_path)
    (tmp_path/'diagrams/ch02_d01.svg').unlink()
    with pytest.raises(PipelineError):
        assemble_course(draft, diagrams, tmp_path)


async def test_provider_uses_deepseek_contract_and_rejects_truncation(monkeypatch):
    from src.services.thinktogether.course_generation.provider import complete
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-private-key')
    def reply(request):
        import json
        body = json.loads(request.content)
        assert body['model'] == 'deepseek-flash'
        assert body['thinking'] == {'type': 'enabled'}
        assert body['reasoning_effort'] == 'medium'
        return Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]})
    async with AsyncClient(transport=MockTransport(reply)) as client:
        with pytest.raises(PipelineError):
            await complete('system', 'source', client=client)


async def test_endpoint_requires_content_permission_and_serves_private_svg(db, org, monkeypatch, draft, diagram_csv):
    from src.routers.thinktogether.course_generation import router
    from src.services.thinktogether.common import get_context
    from src.core.events.database import get_db_session
    from src.services.thinktogether.course_generation import generator
    from src.services.thinktogether.jobs import run_job
    app = FastAPI()
    app.include_router(router, prefix='/api/v1/tt')
    context = SimpleNamespace(org_id=org.id, user=SimpleNamespace(id=101), can=lambda p: True)
    app.dependency_overrides[get_context] = lambda: context
    app.dependency_overrides[get_db_session] = lambda: db
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    calls = []
    async def complete(system, user, **kwargs):
        calls.append(user)
        return draft if len(calls) == 1 else diagram_csv
    monkeypatch.setattr(generator, 'complete', complete)
    async def worker_context(db, job):
        return context
    monkeypatch.setattr(generator, 'worker_context', worker_context)
    from src.services.thinktogether.course_materials import extract_material, save_material
    data = ('Текст об основе, механизме и самостоятельной практике. '*20).encode()
    material = await save_material(db, context, 'source.txt', data, extract_material(data, 'source.txt'))
    body = {'title': 'Тест', 'material_id': material['id'], 'idempotency_key': 'first'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/tt/content/course-pipeline', json=body)
        assert response.status_code == 202
        identifier = response.json()['id']
        assert (await client.post('/api/v1/tt/content/course-pipeline', json=body)).json()['id'] == identifier
        assert (await client.post('/api/v1/tt/content/course-pipeline', json={**body, 'title': 'Другой курс'})).status_code == 409
        job = await generator.get_run(db, context, identifier)
        assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
        job.available_at = generator.utcnow()
        db.add(job)
        await db.commit()
        assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
        job.available_at = generator.utcnow()
        db.add(job)
        await db.commit()
        assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
        result = (await client.get(f'/api/v1/tt/content/course-pipeline/{identifier}')).json()
        assert result['status'] == 'done' and len(calls) == 2
        assert 'TT:DIAGRAM' not in result['markdown']
        assert len(result['diagrams']) == 3
        svg = await client.get(result['diagrams'][0]['url'])
        assert svg.status_code == 200 and svg.headers['content-type'].startswith('image/svg+xml')
        assert svg.headers['cache-control'] == 'private, no-store'
        context.can = lambda p: False
        assert (await client.get(result['diagrams'][0]['url'])).status_code == 403
        assert (await client.post('/api/v1/tt/content/course-pipeline', json=body)).status_code == 403
        context.can = lambda p: True
        context.org_id += 100
        assert (await client.get(result['diagrams'][0]['url'])).status_code == 404


def test_source_selection_is_bounded_and_reports_partial_coverage():
    from src.services.thinktogether.course_generation.source import prepare_source, clean_text, MAX_CONTEXT_CHARS
    assert clean_text('Сло-\nво\x00\u00ad\r\n\r\nабзац') == 'Слово\n\nабзац'
    chunks = [{'id': str(n), 'page': n+1, 'text': ('Текст основного раздела. '*100)} for n in range(30)]
    chunks[20]['text'] = 'Квантование моделей. '*100
    material = SimpleNamespace(payload={'chunks': chunks, 'characters': sum(len(c['text']) for c in chunks)})
    source, coverage = prepare_source(material, 'Квантование')
    assert 'Квантование' in source
    assert coverage['partial'] and coverage['selected_chunks'] <= 12
    assert coverage['selected_characters'] <= MAX_CONTEXT_CHARS
    assert coverage['total_chunks'] == 30
    assert '[S001]' in source


def test_large_material_preserves_sentence_boundaries_without_duplicate_excerpts():
    from src.services.thinktogether.course_materials import extract_material
    text = ' '.join(f'Предложение {n} объясняет учебный механизм и его применение.' for n in range(150))
    result = extract_material(text.encode(), 'source.txt')
    chunks = [chunk['text'] for chunk in result['chunks']]
    assert len(chunks) > 1 and all(len(chunk) <= 2600 for chunk in chunks)
    assert all(chunk.endswith('.') for chunk in chunks)
    assert ' '.join(' '.join(chunks).split()) == ' '.join(text.split())


def test_pptx_extraction_preserves_slide_order_and_rejects_xml_entities():
    from zipfile import ZipFile
    from src.services.thinktogether.course_materials import extract_material
    def presentation(text):
        output = io.BytesIO()
        with ZipFile(output, 'w') as archive:
            archive.writestr('ppt/presentation.xml', '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="r10"/><p:sldId id="2" r:id="r1"/><p:sldId id="3" r:id="r2"/></p:sldIdLst></p:presentation>')
            archive.writestr('ppt/_rels/presentation.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="r{n}" Target="slides/slide{n}.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/>' for n in (1, 2, 10))+'</Relationships>')
            for n in (10, 2, 1):
                archive.writestr(f'ppt/slides/slide{n}.xml', text.replace('NUMBER', str(n)))
            archive.writestr('ppt/slides/slide99.xml', text.replace('NUMBER', '99'))
        return output.getvalue()
    xml = '<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:r><a:t>Слайд NUMBER</a:t></a:r></a:p>'
    result = extract_material(presentation(xml), 'deck.pptx')
    assert [chunk['text'] for chunk in result['chunks']] == ['Слайд 10', 'Слайд 1', 'Слайд 2']
    assert result['pages'] == 3
    with pytest.raises(ValueError):
        extract_material(presentation('<!DOCTYPE a [<!ENTITY x "bad">]>'+xml), 'bad.pptx')


async def test_failed_diagrams_preserve_draft_without_publishing_or_retrying(db, org, monkeypatch, draft):
    from src.services.thinktogether.course_generation import generator
    from src.services.thinktogether.course_materials import extract_material, save_material
    from src.services.thinktogether.jobs import run_job
    context = SimpleNamespace(org_id=org.id, user=SimpleNamespace(id=101), can=lambda p: True)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    data = ('Исходный учебный материал о трёх последовательных темах. '*20).encode()
    material = await save_material(db, context, 'source.txt', data, extract_material(data, 'source.txt'))
    result = await generator.start(db, context, generator.CourseInput(title='Тема', material_id=material['id'], idempotency_key='failure'))
    job = await generator.get_run(db, context, result['id'])
    calls = []
    async def complete(*args, **kwargs):
        calls.append(args)
        return draft if len(calls) == 1 else 'broken CSV'
    async def authorized(*args):
        return context
    monkeypatch.setattr(generator, 'complete', complete)
    monkeypatch.setattr(generator, 'worker_context', authorized)
    assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
    job.available_at = generator.utcnow()
    db.add(job)
    await db.commit()
    assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
    await db.refresh(job)
    result = generator.snapshot(job)
    assert result['status'] == 'error' and result['markdown'] is None
    assert job.payload['draft'] == draft and not job.payload.get('artifacts')
    assert result['draft_url'] and len(calls) == 2
    assert not await run_job(db, job.id, {generator.KIND: generator.generate_step})
    assert len(calls) == 2


async def test_worker_lease_recovery_and_render_retry_do_not_repeat_completed_calls(db, org, monkeypatch, draft, diagram_csv):
    from datetime import timedelta
    from src.services.thinktogether.course_generation import generator
    from src.services.thinktogether.course_materials import extract_material, save_material
    from src.services.thinktogether.jobs import run_job, recover_leases
    context = SimpleNamespace(org_id=org.id, user=SimpleNamespace(id=101), can=lambda p: True)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    data = ('Учебный материал о принципах, применении и практике. '*20).encode()
    material = await save_material(db, context, 'source.txt', data, extract_material(data, 'source.txt'))
    result = await generator.start(db, context, generator.CourseInput(title='Тема', material_id=material['id'], idempotency_key='recover'))
    job = await generator.get_run(db, context, result['id'])
    calls = []
    async def complete(*args, **kwargs):
        calls.append(args)
        return draft if len(calls) == 1 else diagram_csv
    async def authorized(*args):
        return context
    monkeypatch.setattr(generator, 'complete', complete)
    monkeypatch.setattr(generator, 'worker_context', authorized)
    assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
    # Simulate a worker that died after claiming the second step, before a call.
    job.status, job.attempts = 'running', 1
    job.lease_until = generator.utcnow()-timedelta(seconds=1)
    db.add(job)
    await db.commit()
    assert await recover_leases(db) == 1
    await db.commit()
    assert job.status == 'retry'
    assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
    await db.refresh(job)
    assert job.payload['stage'] == 'render' and len(calls) == 2
    real_render = generator.render_package
    def unavailable_disk(*args):
        raise OSError('temporary disk failure')
    monkeypatch.setattr(generator, 'render_package', unavailable_disk)
    job.available_at = generator.utcnow()
    db.add(job)
    await db.commit()
    assert not await run_job(db, job.id, {generator.KIND: generator.generate_step})
    monkeypatch.setattr(generator, 'render_package', real_render)
    await db.refresh(job)
    job.available_at = generator.utcnow()
    db.add(job)
    await db.commit()
    assert await run_job(db, job.id, {generator.KIND: generator.generate_step})
    await db.refresh(job)
    assert generator.snapshot(job)['status'] == 'done' and len(calls) == 2


async def test_short_source_is_an_http_validation_error(db, org, monkeypatch):
    from fastapi import HTTPException
    from src.services.thinktogether.course_generation import generator
    from src.services.thinktogether.course_materials import extract_material, save_material
    context = SimpleNamespace(org_id=org.id, user=SimpleNamespace(id=101), can=lambda p: True)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    data = 'Очень короткий текст.'.encode()
    material = await save_material(db, context, 'source.txt', data, extract_material(data, 'source.txt'))
    with pytest.raises(HTTPException) as error:
        await generator.start(db, context, generator.CourseInput(title='Тема', material_id=material['id'], idempotency_key='short'))
    assert error.value.status_code == 422


async def test_provider_missing_key_and_http_errors_are_controlled(monkeypatch):
    from src.services.thinktogether.course_generation.provider import complete
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    with pytest.raises(PipelineError, match='DEEPSEEK_API_KEY'):
        await complete('system', 'source')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'private-test-key')
    async with AsyncClient(transport=MockTransport(lambda request: Response(429, text='sensitive provider error'))) as client:
        with pytest.raises(PipelineError) as error:
            await complete('system', 'source', client=client)
    assert 'sensitive' not in str(error.value) and 'private-test-key' not in str(error.value)
