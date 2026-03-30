from __future__ import annotations

from pathlib import Path

from search_engine import add_to_index


BASE_DIR = Path(__file__).resolve().parent
INGEST_DIR = BASE_DIR / 'uploads'
INGEST_DIR.mkdir(exist_ok=True)


def ingest_text(title: str, content: str, metadata: dict | None = None, file_id: str | None = None):
    book_id = file_id or f'book_{abs(hash((title, content))) % (10 ** 12)}'
    file_stub = INGEST_DIR / f'{book_id}.txt'
    file_stub.write_text(content, encoding='utf-8')
    payload = {
        'title': title,
        'content': content,
        'format': 'txt',
        'file_path': str(file_stub),
        'source_filename': file_stub.name,
        'cache_path': str(INGEST_DIR / f'{book_id}.cache.txt'),
        'metadata': metadata or {'ingest_origin': 'api'},
        'file_hash': book_id,
    }
    record = add_to_index(payload, file_id=book_id)
    return {'status': 'ok', 'record': record}
