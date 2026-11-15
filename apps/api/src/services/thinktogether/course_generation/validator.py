import csv
import io
import re
from dataclasses import dataclass


class PipelineError(ValueError):
    """Invalid agent output or an incomplete artifact package."""


class ProviderError(PipelineError):
    pass


class SourceInsufficient(PipelineError):
    pass


HEADER = 'diagram_id,record_type,element_id,label,from_id,to_id,group,order'
COURSE = '<!-- TT:COURSE version="1" chapters="3" -->'
DIAGRAM = re.compile(r'<!-- TT:DIAGRAM id="(ch0[123]_d01)" type="(flowchart|hierarchy|comparison)" -->')
CHAPTERS = ('ch01', 'ch02', 'ch03')


@dataclass(frozen=True)
class Marker:
    identifier: str
    kind: str
    text: str


def validate_course(markdown: str) -> dict[str, Marker]:
    if markdown.strip() == 'ERR_SOURCE_INSUFFICIENT':
        raise SourceInsufficient('В источнике недостаточно материала для трёх глав.')
    lines = markdown.splitlines()
    if not lines or lines[0] != COURSE or len(markdown) > 100_000:
        raise PipelineError('Некорректный заголовок курса.')
    if any(re.match(r'^\s*(`{3,}|~{3,})', line) for line in lines):
        raise PipelineError('Маркеры курса нельзя заключать в блок кода.')
    h1 = [i for i, line in enumerate(lines) if re.match(r'^#\s+\S', line)]
    headings = [line for line in lines if re.match(r'^##\s', line)]
    if len(h1) != 1 or len(headings) != 3:
        raise PipelineError('Требуется название курса и ровно три главы.')
    markers, active, completed, seen = {}, None, [], []
    for index, line in enumerate(lines):
        if 'TT:' not in line:
            continue
        seen.append(line)
        if line == COURSE and index == 0:
            continue
        start = re.fullmatch(r'<!-- TT:CHAPTER_START id="(ch0[123])" -->', line)
        end = re.fullmatch(r'<!-- TT:CHAPTER_END id="(ch0[123])" -->', line)
        diagram = DIAGRAM.fullmatch(line)
        if start and active is None and len(completed) < 3 and start[1] == CHAPTERS[len(completed)]:
            if not completed and h1[0] >= index:
                raise PipelineError('Название должно находиться перед главами.')
            active = (start[1], index)
        elif end and active and end[1] == active[0]:
            identifier, first = active
            chapter = lines[first+1:index]
            number = len(completed)+1
            if len([s for s in chapter if re.match(r'^##\s', s)]) != 1 or not any(re.match(fr'^## Глава {number}\.\s+\S', s) for s in chapter):
                raise PipelineError('Неверный заголовок главы.')
            if identifier+'_d01' not in markers:
                raise PipelineError('В каждой главе нужна одна схема.')
            completed.append(identifier)
            active = None
        elif diagram and active and diagram[1] == active[0]+'_d01' and diagram[1] not in markers:
            markers[diagram[1]] = Marker(diagram[1], diagram[2], line)
        else:
            raise PipelineError('Неверный, повторный или вложенный маркер курса.')
    if active or tuple(completed) != CHAPTERS or len(seen) != 10 or len(markers) != 3:
        raise PipelineError('Требуется ровно три пары глав и три схемы.')
    return markers


def _graph(kind, nodes, edges):
    ids = {row['element_id'] for row in nodes}
    pairs = [(row['from_id'], row['to_id']) for row in edges]
    if len(set(pairs)) != len(pairs) or any(a not in ids or b not in ids or a == b for a, b in pairs):
        raise PipelineError('Рёбра должны связывать разные существующие узлы без повторов.')
    if kind == 'comparison':
        if any(row['group'] not in {'left', 'right'} for row in nodes) or any(sum(row['group'] == side for row in nodes) < 2 for side in ('left', 'right')):
            raise PipelineError('Для сравнения нужны две группы минимум по два узла.')
        return
    if any(row['group'] != 'main' for row in nodes):
        raise PipelineError('Узлы процесса и дерева должны принадлежать группе main.')
    incoming = {key: 0 for key in ids}
    children = {key: [] for key in ids}
    for a, b in pairs:
        incoming[b] += 1
        children[a].append(b)
    roots = [key for key in ids if incoming[key] == 0]
    if len(roots) != 1 or len(pairs) != len(nodes)-1 or any(value > 1 for value in incoming.values()):
        raise PipelineError('Схема должна быть связным направленным процессом или деревом.')
    visited, pending = set(), roots[:]
    while pending:
        key = pending.pop()
        if key in visited:
            raise PipelineError('Циклы в схеме недопустимы.')
        visited.add(key)
        pending.extend(children[key])
    if visited != ids or (kind == 'flowchart' and any(len(value) > 1 for value in children.values())):
        raise PipelineError('Процесс должен идти последовательно, дерево должно быть связным.')
    if kind == 'flowchart':
        ordered = [row['element_id'] for row in sorted(nodes, key=lambda row: row['order'])]
        if set(pairs) != set(zip(ordered, ordered[1:])):
            raise PipelineError('Порядок процесса должен совпадать с направлением рёбер.')


def validate_diagrams(text: str, markers: dict[str, Marker]) -> dict:
    if not text or len(text) > 50_000 or text.splitlines()[0] != HEADER:
        raise PipelineError('Ожидается CSV с точным заголовком контракта.')
    diagrams = {key: {'kind': marker.kind, 'nodes': [], 'edges': []} for key, marker in markers.items()}
    previous_diagram, previous_type, count = -1, -1, 0
    seen, orders = {key: set() for key in markers}, {key: set() for key in markers}
    try:
        reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
        for row in reader:
            count += 1
            if count > 100 or None in row or any(value is None for value in row.values()):
                raise PipelineError('Некорректное число полей CSV.')
            key, record = row['diagram_id'], row['record_type']
            if key not in diagrams or record not in ('meta', 'node', 'edge'):
                raise PipelineError('Неизвестная схема или тип записи CSV.')
            diagram_index, type_index = list(markers).index(key), ('meta', 'node', 'edge').index(record)
            if diagram_index < previous_diagram or (diagram_index == previous_diagram and type_index < previous_type):
                raise PipelineError('Неверный порядок записей CSV.')
            previous_diagram, previous_type = diagram_index, type_index
            if not re.fullmatch(r'0|[1-9]\d{0,2}', row['order']):
                raise PipelineError('Порядок должен быть целым неотрицательным числом.')
            row['order'] = int(row['order'])
            diagram = diagrams[key]
            if record == 'meta':
                if 'label' in diagram or row['group'] != diagram['kind'] or row['order'] != 0 or any(row[f] for f in ('element_id', 'from_id', 'to_id')):
                    raise PipelineError('Требуется одна корректная meta-запись для каждой схемы.')
                if not row['label'].strip() or len(row['label']) > 180 or re.search(r'[\x00-\x1f]', row['label']):
                    raise PipelineError('Некорректная подпись схемы.')
                diagram['label'] = row['label']
                continue
            element = row['element_id']
            prefix = 'n' if record == 'node' else 'e'
            order_key = (record, row['order'])
            if not re.fullmatch(prefix+r'[1-9]\d?', element) or element in seen[key] or row['order'] < 1 or order_key in orders[key]:
                raise PipelineError('ID элементов и их порядок должны быть уникальными.')
            seen[key].add(element)
            orders[key].add(order_key)
            if record == 'node':
                if not row['label'].strip() or len(row['label']) > 45 or row['from_id'] or row['to_id'] or re.search(r'[\x00-\x1f]', row['label']):
                    raise PipelineError('Некорректная подпись или поля узла.')
                diagram['nodes'].append(row)
            else:
                if row['label'] or row['group']:
                    raise PipelineError('Подпись и группа ребра должны быть пустыми.')
                diagram['edges'].append(row)
    except (csv.Error, UnicodeError) as exc:
        raise PipelineError('Не удалось прочитать CSV.') from exc
    for diagram in diagrams.values():
        if 'label' not in diagram or not 3 <= len(diagram['nodes']) <= 6:
            raise PipelineError('В каждой схеме нужна подпись и от трёх до шести узлов.')
        _graph(diagram['kind'], diagram['nodes'], diagram['edges'])
    return diagrams
