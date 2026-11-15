"""Versioned Markdown directives and strictly validated exercise CSV insertion."""
import csv
import io
import json
import re

from src.services.thinktogether.learning_content import validate_item

PROTOCOL = 'tt-course-md-v1'
TAGS = {'explain', 'example', 'reflection', 'exercise'}
CSV_HEADERS = ['id', 'kind', 'prompt', 'choices_json', 'answer_key_json', 'explanation', 'source_refs_json']


def build_exercise_csv(exercises):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    for value in exercises:
        row = value.model_dump() if hasattr(value, 'model_dump') else value
        writer.writerow([row['id'], row['kind'], row['prompt'],
            json.dumps(row['choices'], ensure_ascii=False), json.dumps(row['answer_key'], ensure_ascii=False),
            row['explanation'], json.dumps(row.get('source_refs', []), ensure_ascii=False)])
    return output.getvalue()


def _markdown_lines(markdown, *, keepends=False):
    """Identify directive-bearing lines without interpreting literal fences."""
    fence = None
    for line in markdown.splitlines(keepends=keepends):
        stripped = line.strip()
        code_line = fence is not None or stripped.startswith(('```', '~~~'))
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
        yield line, code_line


def parse_blocks(markdown, *, compiled=False):
    blocks, plain, active, body, ids = [], [], None, [], set()
    def flush():
        if plain:
            blocks.append({'type': 'markdown', 'text': '\n'.join(plain)})
            plain.clear()
    for line, code_line in _markdown_lines(markdown):
        stripped = line.strip()
        if not code_line and stripped.startswith(':::'):
            if stripped == ':::':
                if active is None:
                    raise ValueError('Unmatched Markdown block closing tag.')
                text = '\n'.join(body).strip()
                if active['type'] == 'exercise':
                    expected = rf'\{{\{{item:[0-9a-f-]{{36}}\}}\}}' if compiled else r'\{\{csv:'+re.escape(active['id'])+r'\}\}'
                    instructions = '\n'.join(line for line, code in _markdown_lines(text) if not code)
                    if len(re.findall(expected, instructions)) != 1:
                        raise ValueError('Each exercise block needs exactly one matching insertion marker.')
                elif not text:
                    raise ValueError('Text blocks must not be empty.')
                blocks.append({**active, 'text': text})
                active, body = None, []
                continue
            if active:
                raise ValueError('Nested Markdown directives are not supported.')
            flush()
            parts = stripped[3:].split()
            if not parts or parts[0] not in TAGS or len(parts) != (2 if parts[0] == 'exercise' else 1):
                raise ValueError('Unknown or malformed course Markdown directive.')
            active = {'type': parts[0]}
            if parts[0] == 'exercise':
                if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', parts[1]) or parts[1] in ids:
                    raise ValueError('Exercise IDs must be unique safe identifiers.')
                active['id'] = parts[1]
                ids.add(parts[1])
        elif active:
            body.append(line)
        else:
            plain.append(line)
    if active:
        raise ValueError('Unclosed course Markdown directive.')
    flush()
    if not ids or len(ids) > 10:
        raise ValueError('A chapter needs 1–10 exercise insertion markers.')
    return blocks


def parse_exercise_csv(text, expected_ids):
    if not isinstance(text, str) or len(text) > 100000:
        raise ValueError('Exercise CSV is too large.')
    reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
    if reader.fieldnames != CSV_HEADERS:
        raise ValueError('Unexpected exercise CSV columns.')
    rows = []
    for row in reader:
        if len(rows) >= 10 or None in row or any(v is None for v in row.values()):
            raise ValueError('Malformed exercise CSV row.')
        if any(value.lstrip().startswith(('=', '+', '-', '@')) for value in row.values()):
            raise ValueError('Spreadsheet formulas are not allowed in exercise CSV.')
        if row['kind'] not in {'single_choice', 'multiple_choice'}:
            raise ValueError('Demo CSV supports single and multiple choice exercises.')
        item = {'id': row['id'], 'title': row['prompt'][:100], 'prompt': row['prompt'], 'kind': row['kind'],
                'choices': json.loads(row['choices_json']), 'answer_key': json.loads(row['answer_key_json']),
                'explanation': row['explanation'], 'source_refs': json.loads(row['source_refs_json'])}
        if not isinstance(item['source_refs'], list) or len(item['source_refs']) > 6:
            raise ValueError('Invalid CSV material references.')
        validate_item({**item, 'family_id': 'validated-csv', 'rubric': {}, 'estimated_minutes': 3})
        if not item['prompt'].strip() or not item['explanation'].strip():
            raise ValueError('Exercise prompts and explanations are required.')
        rows.append(item)
    if len(rows) != len(expected_ids) or {row['id'] for row in rows} != set(expected_ids):
        raise ValueError('CSV must fill each Markdown exercise marker exactly once.')
    return rows


def insert_exercises(markdown, bindings):
    blocks = parse_blocks(markdown)
    expected = {b['id'] for b in blocks if b['type'] == 'exercise'}
    if expected != set(bindings):
        raise ValueError('Exercise bindings do not match the chapter markers.')
    for key, item_id in bindings.items():
        if not re.fullmatch(r'[0-9a-f-]{36}', item_id):
            raise ValueError('Invalid exercise record identifier.')
    # Preserve original Markdown/newlines. Fenced examples are literal text,
    # including markers that happen to name a real exercise in this chapter.
    lines, exercise = [], None
    for line, code_line in _markdown_lines(markdown, keepends=True):
        stripped = line.strip()
        if not code_line:
            if stripped.startswith(':::'):
                parts = stripped[3:].split()
                exercise = parts[1] if parts and parts[0] == 'exercise' else None
            elif exercise:
                line = line.replace('{{csv:'+exercise+'}}', '{{item:'+bindings[exercise]+'}}')
            if '{{csv:' in line:
                raise ValueError('Unresolved CSV insertion marker.')
        lines.append(line)
    result = ''.join(lines)
    parse_blocks(result, compiled=True)
    return result
