from html import escape
from pathlib import Path
import re
import textwrap
from xml.etree import ElementTree as ET

from .validator import PipelineError, validate_course


def render_svg(diagram):
    nodes = sorted(diagram['nodes'], key=lambda row: row['order'])
    kind, positions = diagram['kind'], {}
    width, box_width, box_height = 760, 300, 94
    if kind == 'flowchart':
        positions = {row['element_id']: (230, 32+i*140) for i, row in enumerate(nodes)}
    elif kind == 'comparison':
        for side, x in (('left', 40), ('right', 420)):
            for i, row in enumerate(row for row in nodes if row['group'] == side):
                positions[row['element_id']] = (x, 32+i*140)
    else:
        children = {row['element_id']: [] for row in nodes}
        targets = {row['to_id'] for row in diagram['edges']}
        for edge in diagram['edges']:
            children[edge['from_id']].append(edge['to_id'])
        root = next(key for key in children if key not in targets)
        levels, queue = {}, [(root, 0)]
        while queue:
            key, depth = queue.pop(0)
            levels.setdefault(depth, []).append(key)
            queue.extend((child, depth+1) for child in children[key])
        columns = max(len(level) for level in levels.values())
        width = max(width, columns*340+40)
        for depth, level in levels.items():
            for i, key in enumerate(level):
                positions[key] = (width*(i+0.5)/len(level)-box_width/2, 32+depth*150)
    height = int(max(y for _, y in positions.values())+box_height+32)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
        f'<title id="title">{escape(diagram["label"])}</title>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#475569"/></marker></defs>',
        f'<rect width="{width}" height="{height}" rx="16" fill="#f8fafc"/>']
    for edge in diagram['edges']:
        ax, ay = positions[edge['from_id']]
        bx, by = positions[edge['to_id']]
        if kind == 'comparison' and ax != bx:
            x1, y1 = (ax+box_width if ax < bx else ax), ay+box_height/2
            x2, y2 = (bx if ax < bx else bx+box_width), by+box_height/2
        else:
            x1, y1, x2, y2 = ax+box_width/2, ay+box_height, bx+box_width/2, by
        parts.append(f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>')
    for node in nodes:
        x, y = positions[node['element_id']]
        color = '#ecfdf5' if node['group'] == 'right' else '#eff6ff'
        parts.append(f'<rect x="{x}" y="{y}" width="{box_width}" height="{box_height}" rx="10" fill="{color}" stroke="#64748b"/>')
        lines = textwrap.wrap(node['label'], width=26, break_long_words=True, break_on_hyphens=False)
        start = y+box_height/2-(len(lines)-1)*12+6
        parts.append(f'<text text-anchor="middle" font-family="Arial, sans-serif" font-size="18" fill="#0f172a">')
        for index, line in enumerate(lines):
            parts.append(f'<tspan x="{x+box_width/2}" y="{start+index*24}">{escape(line)}</tspan>')
        parts.append('</text>')
    parts.append('</svg>')
    return ''.join(parts)


def assemble_course(draft, diagrams, directory):
    markers = validate_course(draft)
    if set(markers) != set(diagrams):
        raise PipelineError('Набор схем не совпадает с курсом.')
    for key in markers:
        file = Path(directory)/'diagrams'/f'{key}.svg'
        if not file.is_file() or not file.stat().st_size:
            raise PipelineError('Не создан один из SVG-файлов.')
        try:
            if ET.fromstring(file.read_text(encoding='utf-8')).tag != '{http://www.w3.org/2000/svg}svg':
                raise PipelineError('Некорректный SVG-файл.')
        except ET.ParseError as exc:
            raise PipelineError('Некорректный SVG-файл.') from exc
    final = draft
    for key, marker in markers.items():
        label = re.sub(r'([\\\[\]])', r'\\\1', diagrams[key]['label'])
        final = final.replace(marker.text, f'![{label}](./diagrams/{key}.svg)', 1)
    if 'TT:DIAGRAM' in final or len(re.findall(r'^## Глава [123]\. ', final, re.M)) != 3:
        raise PipelineError('Итоговый курс не прошёл проверку.')
    return final


def render_package(draft, diagrams, directory):
    directory = Path(directory)
    folder = directory/'diagrams'
    folder.mkdir(parents=True, exist_ok=True)
    (directory/'course.draft.md').write_text(draft, encoding='utf-8')
    for key, diagram in diagrams.items():
        if key not in validate_course(draft):
            raise PipelineError('Неизвестный ID схемы.')
        (folder/f'{key}.svg').write_text(render_svg(diagram), encoding='utf-8')
    final = assemble_course(draft, diagrams, directory)
    (directory/'course.final.md').write_text(final, encoding='utf-8')
    return {path.relative_to(directory).as_posix(): path.read_text(encoding='utf-8')
            for path in sorted(directory.rglob('*')) if path.is_file()}
