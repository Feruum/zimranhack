from pathlib import Path


def _system(name):
    text = Path(__file__).with_name(name).read_text(encoding='utf-8-sig')
    return text.split('USER PROMPT TEMPLATE')[0].removeprefix('SYSTEM PROMPT').strip()


COURSE_ARCHITECT = _system('course_architect.txt')
DIAGRAM_ARCHITECT = _system('diagram_architect.txt')


def course_input(title, audience, goal, source):
    return (f'SOURCE_TITLE: {title}\nTARGET_AUDIENCE: {audience}\nLEARNING_GOAL: {goal}\n'
        f'OUTPUT_LANGUAGE: русский\n\n<SOURCE_MATERIAL>\n{source}\n</SOURCE_MATERIAL>\n\n'
        'Сгенерируй курс по системным правилам. Для MVP строго три главы и одна схема-заглушка в каждой.')


def diagram_input(draft):
    return f'<COURSE_DRAFT_MD>\n{draft}\n</COURSE_DRAFT_MD>\n\nСоздай CSV для всех маркеров TT:DIAGRAM строго по системному контракту.'
