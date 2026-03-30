from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from service_paths import CHUNK_DB_FILE, ensure_runtime_dirs


ensure_runtime_dirs()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CHUNK_DB_FILE)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            content TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chunks_book_id ON chunks(book_id, position)')
    return conn


def chunk_store_mtime() -> float | None:
    try:
        return Path(CHUNK_DB_FILE).stat().st_mtime
    except OSError:
        return None


def upsert_book_chunks(book_id: str, chunks: list[str], chunk_ids: list[str]) -> None:
    if not book_id:
        return
    ensure_runtime_dirs()
    now = time.time()
    with _connect() as conn:
        conn.execute('DELETE FROM chunks WHERE book_id = ?', (book_id,))
        conn.executemany(
            'INSERT INTO chunks(chunk_id, book_id, position, content, updated_at) VALUES (?, ?, ?, ?, ?)',
            [
                (chunk_id, book_id, idx, str(chunk or ''), now)
                for idx, (chunk_id, chunk) in enumerate(zip(chunk_ids, chunks))
            ],
        )
        conn.commit()


def delete_book_chunks(book_id: str) -> None:
    if not book_id:
        return
    with _connect() as conn:
        conn.execute('DELETE FROM chunks WHERE book_id = ?', (book_id,))
        conn.commit()


def fetch_chunks(chunk_ids: list[str]) -> dict[str, str]:
    cleaned_ids = [str(item) for item in chunk_ids if str(item).strip()]
    if not cleaned_ids:
        return {}
    placeholders = ','.join('?' for _ in cleaned_ids)
    with _connect() as conn:
        rows = conn.execute(
            f'SELECT chunk_id, content FROM chunks WHERE chunk_id IN ({placeholders})',
            cleaned_ids,
        ).fetchall()
    return {str(chunk_id): str(content or '') for chunk_id, content in rows}
