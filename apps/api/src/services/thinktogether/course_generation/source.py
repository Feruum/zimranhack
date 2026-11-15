import re
import unicodedata

from .validator import SourceInsufficient

MAX_CONTEXT_CHARS = 28000


def clean_text(text):
    text = unicodedata.normalize('NFC', text).replace('\r\n', '\n').replace('\r', '\n').replace('\u00ad', '')
    text = re.sub(r'(?<=\w)-\n(?=\w)', '', text)
    text = ''.join(char for char in text if char in '\n\t' or not unicodedata.category(char).startswith('C'))
    text = re.sub(r'[^\S\n]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def prepare_source(material, query):
    chunks = material.payload['chunks']
    tokens = set(re.findall(r'\w{3,}', query.casefold()))
    ranked = sorted(range(len(chunks)), key=lambda index: -len(tokens.intersection(re.findall(r'\w{3,}', chunks[index]['text'].casefold()))))
    chosen, size = {}, 0
    for index in ranked:
        text = clean_text(chunks[index]['text'])
        if not text:
            continue
        if len(chosen) >= 12 or size+len(text) > MAX_CONTEXT_CHARS:
            continue
        chosen[index], size = text, size+len(text)
    if size < 200:
        raise SourceInsufficient('В документе слишком мало извлечённого текста. Загрузите содержательный фрагмент.')
    fragments = [{'id': f'S{number:03}', 'page': chunks[index]['page'], 'text': text}
                 for number, (index, text) in enumerate(sorted(chosen.items()), 1)]
    coverage = {'selected_chunks': len(fragments), 'total_chunks': len(chunks), 'selected_characters': size,
        'total_characters': material.payload['characters'], 'partial': len(chosen) < len(chunks),
        'message': 'Использована подборка фрагментов по теме; документ обработан частично.' if len(chosen) < len(chunks)
                   else 'Использован весь извлечённый текст документа.'}
    return '\n\n'.join(f'[{fragment["id"]}] (страница {fragment["page"]})\n{fragment["text"]}' for fragment in fragments), coverage
