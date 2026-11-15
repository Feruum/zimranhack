"""Private, immutable extracted course materials in existing durable job rows."""
import hashlib
import posixpath
import re
from io import BytesIO
from pathlib import PurePath
from defusedxml import ElementTree
from zipfile import ZipFile

from fastapi import HTTPException
from sqlmodel import select

from src.db.thinktogether.jobs import TTJob
from src.services.thinktogether.common import require_permission, utcnow
from src.services.thinktogether.jobs import enqueue_job

KIND = 'content.material'
MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 2_000_000
SUFFIXES = {'.pdf', '.docx', '.pptx', '.txt', '.md', '.csv', '.json'}


def _presentation_slides(archive):
    def read_xml(name):
        entry = archive.getinfo(name)
        if entry.file_size > 1_000_000 or entry.flag_bits & 1:
            raise ValueError('Invalid or oversized PPTX manifest.')
        return ElementTree.fromstring(archive.read(entry))
    presentation = read_xml('ppt/presentation.xml')
    relationships = read_xml('ppt/_rels/presentation.xml.rels')
    by_id = {entry.attrib.get('Id'): entry.attrib for entry in relationships}
    namespace = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
    relationship_id = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    slide_ids = presentation.findall(namespace+'sldIdLst/'+namespace+'sldId')
    if not 1 <= len(slide_ids) <= 300:
        raise ValueError('Use a presentation with 1–300 slides.')
    slides = []
    for slide in slide_ids:
        relationship = by_id.get(slide.attrib.get(relationship_id), {})
        target = relationship.get('Target', '')
        name = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'ppt/'+target)
        if relationship.get('TargetMode') == 'External' or not relationship.get('Type', '').endswith('/slide') or not re.fullmatch(r'ppt/slides/[^/]+\.xml', name):
            raise ValueError('Invalid slide relationship in PPTX.')
        slides.append(archive.getinfo(name))
    if sum(entry.file_size for entry in slides) > 8_000_000:
        raise ValueError('The presentation text content is too large.')
    return slides


def _text_chunks(text, limit=2600):
    remaining = text.strip()
    while remaining:
        end = min(limit, len(remaining))
        if len(remaining) > limit:
            window = remaining[:limit+1]
            for pattern in (r'\n\s*\n', r'(?<=[.!?])\s+', r'\s+'):
                boundaries = [match.start() for match in re.finditer(pattern, window) if match.start() >= limit//2]
                if boundaries:
                    end = boundaries[-1]
                    break
        yield remaining[:end].strip()
        remaining = remaining[end:].lstrip()


def extract_material(data, filename):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Choose a nonempty material smaller than 10 MB.')
    suffix = PurePath(filename.replace('\\', '/')).suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError('Supported files: PDF, DOCX, PPTX, TXT, Markdown, CSV and JSON.')
    pages = []
    if suffix == '.pdf':
        if not data.startswith(b'%PDF-'):
            raise ValueError('The PDF file header is invalid.')
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ValueError('Use an unencrypted PDF with selectable text.')
        if len(reader.pages) > 1200:
            raise ValueError('Use a PDF with no more than 1200 pages.')
        length = 0
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or '').strip()
            length += len(text)
            if length > MAX_CHARS:
                raise ValueError('The extracted text is too large; upload a shorter selection.')
            if text:
                pages.append((number, text))
    elif suffix == '.pptx':
        with ZipFile(BytesIO(data)) as archive:
            slides = _presentation_slides(archive)
            for number, entry in enumerate(slides, 1):
                if entry.flag_bits & 1:
                    raise ValueError('Use an unencrypted presentation.')
                xml = archive.read(entry)
                if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                    raise ValueError('Unsupported XML declarations in PPTX.')
                root = ElementTree.fromstring(xml)
                namespace = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
                text = '\n'.join(''.join(paragraph.itertext()) for paragraph in root.iter(namespace+'p'))
                if text.strip():
                    pages.append((number, text))
    elif suffix == '.docx':
        with ZipFile(BytesIO(data)) as archive:
            entry = archive.getinfo('word/document.xml')
            if entry.file_size > 8_000_000 or entry.flag_bits & 1:
                raise ValueError('The DOCX document is too large or encrypted.')
            xml = archive.read(entry)
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ValueError('Unsupported XML declarations in DOCX.')
        root = ElementTree.fromstring(xml)
        namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        text = '\n'.join(''.join(p.itertext()) for p in root.iter(namespace+'p'))
        pages = [(1, text)]
    else:
        text = data.decode('utf-8-sig')
        if '\x00' in text:
            raise ValueError('Use a UTF-8 text document.')
        pages = [(1, text)]
    if not pages or not any(text.strip() for _, text in pages):
        raise ValueError('No selectable text found. Convert scanned pages to text with OCR first.')
    if sum(len(text) for _, text in pages) > MAX_CHARS:
        raise ValueError('The extracted text is too large; upload a shorter selection.')
    chunks = []
    for page, text in pages:
        for number, body in enumerate(_text_chunks(text), 1):
            if body:
                chunks.append({'id': f'p{page}-c{number}', 'page': page, 'text': body})
    return {'chunks': chunks, 'pages': len(pages), 'characters': sum(len(t) for _, t in pages)}


def public_material(job):
    p = job.payload
    return {'id': job.job_uuid, 'name': p['name'], 'pages': p['pages'], 'characters': p['characters'],
            'sha256': p['sha256'], 'created_at': job.created_at.isoformat()}


async def save_material(db, ctx, filename, data, extracted):
    require_permission(ctx, 'content')
    digest = hashlib.sha256(data).hexdigest()
    name = filename.replace('\\', '/').rsplit('/', 1)[-1][:200]
    job = await enqueue_job(db, ctx.org_id, KIND, {**extracted, 'name': name,
        'sha256': digest, 'actor_id': ctx.user.id}, 'course-material:'+digest)
    job.status, job.completed_at = 'done', utcnow()
    db.add(job)
    await db.commit()
    return public_material(job)


async def get_materials(db, ctx, identifiers):
    require_permission(ctx, 'content')
    if len(set(identifiers)) != len(identifiers):
        raise HTTPException(422, 'Choose each attached material only once.')
    found = (await db.exec(select(TTJob).where(TTJob.org_id == ctx.org_id, TTJob.kind == KIND,
        TTJob.status == 'done', TTJob.job_uuid.in_(identifiers)))).all() if identifiers else []
    if len(found) != len(identifiers):
        raise HTTPException(404, 'One of the course materials is not available.')
    by_id = {row.job_uuid: row for row in found}
    return [by_id[key] for key in identifiers]


def select_context(materials, inline_text, query, limit=10):
    tokens = set(re.findall(r'[\w-]{3,}', query.casefold()))
    candidates = []
    for material in materials:
        for chunk in material.payload['chunks']:
            candidates.append({'id': material.job_uuid+':'+chunk['id'], 'name': material.payload['name'],
                               'page': chunk['page'], 'text': chunk['text']})
    if inline_text.strip():
        for index in range(0, len(inline_text), 2400):
            candidates.append({'id': 'input:'+str(index//2400+1), 'name': 'Provided text',
                               'page': 1, 'text': inline_text[index:index+2600]})
    def score(chunk):
        words = set(re.findall(r'[\w-]{3,}', chunk['text'].casefold()))
        return len(tokens.intersection(words))
    # Stable ranking and a fixed context budget, with no external embeddings call.
    return sorted(candidates, key=score, reverse=True)[:limit]


def validate_references(references, context, required=False):
    by_id = {chunk['id']: chunk for chunk in context}
    if required and not references:
        raise ValueError('Attached course material requires a verifiable reference.')
    for reference in references:
        ref = reference.model_dump() if hasattr(reference, 'model_dump') else reference
        chunk = by_id.get(ref.get('id'))
        quote = ' '.join(ref.get('quote', '').split())
        if not chunk or len(quote) < 12 or quote not in ' '.join(chunk['text'].split()):
            raise ValueError('AI returned an unsupported material reference.')
