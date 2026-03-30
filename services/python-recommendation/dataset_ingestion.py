from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from book_metadata import get_author
from converter import process_file
from qdrant_gateway import QdrantGateway
from search_engine import add_to_index, chunk_text_content, compute_centroid_embedding, embed_chunks, get_book_embedding, get_text_analyzer, load_index, update_book_record
from service_settings import ServiceSettings


LOGGER = logging.getLogger(__name__)
SUPPORTED_DATASET_EXTENSIONS = {'.txt', '.fb2', '.epub'}
FAILED_TERMINAL = 'terminal'
FAILED_TRANSIENT = 'transient'


class TerminalIngestionError(Exception):
    pass


@dataclass(slots=True)
class IngestionResult:
    status: str
    source_key: str
    book_id: str | None = None
    file_hash: str | None = None
    processed_path: str | None = None
    failed_path: str | None = None
    error: str | None = None


class ProgressLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        payload = {'ts': time.time(), 'event': event, **fields}
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            with open(self.path, 'a', encoding='utf-8') as file_obj:
                file_obj.write(line + '\n')
        LOGGER.log(level, 'ingestion_event=%s data=%s', event, line)


class IngestionStateStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS ingestion_sources (
                    source_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    stable_book_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    file_hash TEXT,
                    last_processed_hash TEXT,
                    processed_path TEXT,
                    failed_path TEXT,
                    format TEXT,
                    title TEXT,
                    author TEXT,
                    chunks_count INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    next_attempt_at REAL,
                    last_attempt_at REAL,
                    last_completed_at REAL,
                    last_discovered_at REAL,
                    size_bytes INTEGER DEFAULT 0,
                    mtime_ns INTEGER DEFAULT 0,
                    qdrant_sync_status TEXT DEFAULT 'pending',
                    qdrant_synced_at REAL,
                    duplicate_of_book_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS completed_hashes (
                    file_hash TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    completed_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_sources_status_next_attempt
                    ON ingestion_sources(status, next_attempt_at);
                '''
            )
            conn.commit()

    def _fetchone(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_source(self, source_key: str) -> dict[str, Any] | None:
        return self._fetchone('SELECT * FROM ingestion_sources WHERE source_key = ?', (source_key,))

    def get_completed_hash(self, file_hash: str) -> dict[str, Any] | None:
        return self._fetchone('SELECT * FROM completed_hashes WHERE file_hash = ?', (file_hash,))

    def ensure_source(self, source_key: str, source_path: str, stable_book_id: str, size_bytes: int, mtime_ns: int) -> dict[str, Any]:
        now = time.time()
        existing = self.get_source(source_key)
        if existing:
            with self._lock, self._connection() as conn:
                conn.execute(
                    '''
                    UPDATE ingestion_sources
                    SET source_path = ?, size_bytes = ?, mtime_ns = ?, last_discovered_at = ?, updated_at = ?
                    WHERE source_key = ?
                    ''',
                    (source_path, int(size_bytes), int(mtime_ns), now, now, source_key),
                )
                conn.commit()
            return self.get_source(source_key) or existing

        with self._lock, self._connection() as conn:
            conn.execute(
                '''
                INSERT INTO ingestion_sources (
                    source_key, source_path, stable_book_id, status, created_at, updated_at,
                    last_discovered_at, size_bytes, mtime_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (source_key, source_path, stable_book_id, 'discovered', now, now, now, int(size_bytes), int(mtime_ns)),
            )
            conn.commit()
        return self.get_source(source_key) or {}

    def update_source(self, source_key: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get_source(source_key)
        fields = dict(fields)
        fields['updated_at'] = time.time()
        assignments = ', '.join(f'{key} = ?' for key in fields)
        values = list(fields.values()) + [source_key]
        with self._lock, self._connection() as conn:
            conn.execute(f'UPDATE ingestion_sources SET {assignments} WHERE source_key = ?', values)
            conn.commit()
        return self.get_source(source_key)

    def record_completed_hash(self, file_hash: str, book_id: str, source_key: str) -> None:
        now = time.time()
        with self._lock, self._connection() as conn:
            conn.execute(
                '''
                INSERT INTO completed_hashes(file_hash, book_id, source_key, completed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    book_id = excluded.book_id,
                    source_key = excluded.source_key,
                    completed_at = excluded.completed_at
                ''',
                (file_hash, book_id, source_key, now),
            )
            conn.commit()

    def list_failed_sources(self) -> list[dict[str, Any]]:
        with self._lock, self._connection() as conn:
            rows = conn.execute('SELECT * FROM ingestion_sources WHERE status = ? ORDER BY updated_at ASC', ('failed',)).fetchall()
        return [dict(row) for row in rows]


class DatasetIngestionCoordinator:
    def __init__(self, settings: ServiceSettings, qdrant: QdrantGateway):
        self.settings = settings
        self.qdrant = qdrant
        self.store = IngestionStateStore(settings.ingestion_state_db_path)
        self.progress = ProgressLog(settings.ingestion_progress_log_path)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._scan_lock = threading.Lock()
        self._foreground_lock = threading.Lock()
        self._foreground_active = 0
        self._foreground_idle_event = threading.Event()
        self._foreground_idle_event.set()
        self._processing_active = threading.Event()
        self._index_write_active = threading.Event()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.ingestion_enabled)

    def start(self) -> bool:
        if not self.enabled or self._thread is not None:
            return False
        if self.settings.ingestion_worker_concurrency > 1:
            LOGGER.warning('INGESTION_WORKER_CONCURRENCY=%s requested; using sequential mode for index safety', self.settings.ingestion_worker_concurrency)
        try:
            self._ensure_directories()
        except OSError as exc:
            LOGGER.warning('Dataset ingestion disabled: cannot initialize dataset directories: %s', exc)
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name='dataset-ingestion', daemon=True)
        self._thread.start()
        LOGGER.info('Dataset ingestion worker started')
        return True

    def begin_foreground_work(self) -> None:
        if not self.settings.ingestion_pause_on_foreground:
            return
        with self._foreground_lock:
            self._foreground_active += 1
            self._foreground_idle_event.clear()

    def end_foreground_work(self) -> None:
        if not self.settings.ingestion_pause_on_foreground:
            return
        with self._foreground_lock:
            self._foreground_active = max(0, self._foreground_active - 1)
            if self._foreground_active == 0:
                self._foreground_idle_event.set()

    def background_pressure(self) -> bool:
        return bool(self.enabled and (self._processing_active.is_set() or self._scan_lock.locked() or self._has_pending_inbox_files()))

    def wait_for_index_write_idle(self, timeout_sec: float = 20.0) -> bool:
        if not self.enabled:
            return True
        deadline = time.time() + max(0.1, float(timeout_sec))
        while self._index_write_active.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            if self._stop_event.wait(min(0.2, remaining)):
                return False
        return True

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=max(2, self.settings.ingestion_poll_interval_sec))
        self._thread = None
        LOGGER.info('Dataset ingestion worker stopped')

    def _run_loop(self) -> None:
        if self.settings.ingestion_scan_on_start:
            self.scan_once()
        while not self._stop_event.wait(self.settings.ingestion_poll_interval_sec):
            if not self.settings.ingestion_watch_mode:
                break
            self._wait_for_foreground_idle()
            self.scan_once()

    def _ensure_directories(self) -> None:
        for directory in (
            self.settings.dataset_inbox_dir,
            self.settings.dataset_processed_dir,
            self.settings.dataset_failed_dir,
            self.settings.dataset_manifests_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def _iter_inbox_files(self) -> Iterable[Path]:
        if not self.settings.dataset_inbox_dir.exists():
            return []
        files = []
        for path in self.settings.dataset_inbox_dir.rglob('*'):
            if path.is_file() and path.suffix.lower() in SUPPORTED_DATASET_EXTENSIONS:
                files.append(path)
        return sorted(files)

    def _has_pending_inbox_files(self) -> bool:
        try:
            for _path in self._iter_inbox_files():
                return True
        except Exception:
            return False
        return False

    def _wait_for_foreground_idle(self) -> None:
        if not self.settings.ingestion_pause_on_foreground:
            return
        while not self._foreground_idle_event.wait(timeout=0.2):
            if self._stop_event.is_set():
                break

    def scan_once(self) -> dict[str, int]:
        if not self.enabled:
            return {'discovered': 0, 'processed': 0, 'failed': 0, 'skipped': 0}
        try:
            self._ensure_directories()
        except OSError as exc:
            LOGGER.warning('Skipping dataset scan: %s', exc)
            return {'discovered': 0, 'processed': 0, 'failed': 0, 'skipped': 0}

        summary = {'discovered': 0, 'processed': 0, 'failed': 0, 'skipped': 0}
        with self._scan_lock:
            for file_path in self._iter_inbox_files():
                self._wait_for_foreground_idle()
                summary['discovered'] += 1
                result = self.process_file(file_path)
                if result.status == 'completed':
                    summary['processed'] += 1
                elif result.status == 'skipped':
                    summary['skipped'] += 1
                else:
                    summary['failed'] += 1
        return summary

    def retry_failed(self) -> int:
        retried = 0
        for record in self.store.list_failed_sources():
            failed_path = Path(str(record.get('failed_path') or ''))
            if not failed_path.exists():
                continue
            source_key = str(record.get('source_key') or failed_path.name)
            restored_path = self._move_file(failed_path, self.settings.dataset_inbox_dir, source_key, preserve_name=True)
            self.store.update_source(
                source_key,
                source_path=str(restored_path),
                status='discovered',
                last_error=None,
                next_attempt_at=None,
                failed_path=None,
            )
            retried += 1
        return retried

    def reindex_path(self, file_path: str) -> IngestionResult:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return IngestionResult(status='failed', source_key=str(file_path), error='File not found')
        source_key = self._source_key_from_path(path, prefer_dataset=False)
        inbox_path = self._prepare_manual_reindex_source(path, source_key)
        return self.process_file(inbox_path, force=True)

    def process_file(self, file_path: Path, force: bool = False) -> IngestionResult:
        self._processing_active.set()
        try:
            return self._process_file_impl(file_path, force=force)
        finally:
            self._processing_active.clear()

    def _process_file_impl(self, file_path: Path, force: bool = False) -> IngestionResult:
        file_path = Path(file_path)
        source_key = self._source_key_from_path(file_path)
        stable_book_id = self._stable_book_id(source_key)
        stat = file_path.stat()
        record = self.store.ensure_source(source_key, str(file_path), stable_book_id, stat.st_size, stat.st_mtime_ns)
        self.progress.emit('file_discovered', source_key=source_key, file_path=str(file_path), status='discovered')

        if not force and self._retry_is_delayed(record):
            return IngestionResult(status='failed', source_key=source_key, error=str(record.get('last_error') or 'retry_delayed'))

        file_hash = self._hash_file(file_path)
        record = self.store.update_source(
            source_key,
            status='hashing',
            file_hash=file_hash,
            source_path=str(file_path),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            last_attempt_at=time.time(),
        ) or record
        self.progress.emit('file_hashed', source_key=source_key, file_path=str(file_path), file_hash=file_hash, status='hashing')

        duplicate = self._find_completed_duplicate(file_hash)
        if duplicate and not force:
            return self._complete_without_reindex(file_path, source_key, file_hash, duplicate)

        try:
            return self._index_file(file_path, source_key, stable_book_id, file_hash)
        except TerminalIngestionError as exc:
            return self._handle_failure(file_path, source_key, file_hash, exc, error_kind=FAILED_TERMINAL)
        except Exception as exc:
            return self._handle_failure(file_path, source_key, file_hash, exc, error_kind=FAILED_TRANSIENT)

    def _index_file(self, file_path: Path, source_key: str, stable_book_id: str, file_hash: str) -> IngestionResult:
        current = self.store.get_source(source_key) or {}
        attempt = int(current.get('retry_count') or 0) + 1
        self.store.update_source(source_key, status='queued')
        self._wait_for_foreground_idle()
        self.store.update_source(source_key, status='parsing')
        self.progress.emit('parse_started', source_key=source_key, file_path=str(file_path), file_hash=file_hash, attempt=attempt, status='parsing')

        book_data = process_file(str(file_path), original_name=source_key)
        if book_data.get('error'):
            raise TerminalIngestionError(str(book_data.get('error')))

        title = str(book_data.get('title') or file_path.stem)
        author = self._author_from_payload(book_data)
        self.store.update_source(source_key, status='parsed', title=title, author=author, format=book_data.get('format'))
        self.progress.emit('parse_completed', source_key=source_key, file_path=str(file_path), file_hash=file_hash, title=title, author=author, status='parsed')

        content = str(book_data.get('content') or '')
        self._wait_for_foreground_idle()
        self.store.update_source(source_key, status='chunking')
        chunks = chunk_text_content(content)
        if not chunks:
            raise TerminalIngestionError('Parsed file does not contain indexable text')
        self.progress.emit('chunking_completed', source_key=source_key, file_path=str(file_path), file_hash=file_hash, title=title, chunks_count=len(chunks), status='chunking')

        self.store.update_source(source_key, status='embedding')
        self.progress.emit(
            'embedding_started',
            source_key=source_key,
            file_path=str(file_path),
            file_hash=file_hash,
            title=title,
            embedding_batch=self.settings.ingestion_embedding_batch_size,
            status='embedding',
        )
        self._wait_for_foreground_idle()
        embeddings = embed_chunks(chunks, batch_size=self.settings.ingestion_embedding_batch_size)
        features = get_text_analyzer().analyze_book(chunks)
        self.progress.emit(
            'embedding_completed',
            source_key=source_key,
            file_path=str(file_path),
            file_hash=file_hash,
            title=title,
            chunks_count=len(chunks),
            embedding_batch=self.settings.ingestion_embedding_batch_size,
            status='embedding',
        )

        metadata = dict(book_data.get('metadata') or {})
        metadata.update(
            {
                'ingest_origin': 'dataset',
                'dataset_source_key': source_key,
                'dataset_source_path': str(file_path),
            }
        )
        book_data['metadata'] = metadata
        book_data['file_hash'] = file_hash
        book_data['file_path'] = str(file_path)
        book_data['source_filename'] = source_key

        self._wait_for_foreground_idle()
        self.store.update_source(source_key, status='indexing')
        self._index_write_active.set()
        try:
            record = add_to_index(
                book_data,
                file_id=stable_book_id,
                prepared_chunks=chunks,
                prepared_embeddings=embeddings,
                prepared_features=features,
            )
            if record is None:
                raise TerminalIngestionError('Failed to persist indexed book')
            self.progress.emit(
                'index_write_completed',
                source_key=source_key,
                book_id=stable_book_id,
                file_path=str(file_path),
                file_hash=file_hash,
                title=title,
                author=author,
                chunks_count=len(chunks),
                status='indexing',
            )

            processed_path = self._move_file(file_path, self.settings.dataset_processed_dir, source_key, file_hash=file_hash)
            updated_metadata = dict(record.get('metadata') or {})
            updated_metadata.update(
                {
                    'dataset_source_key': source_key,
                    'dataset_processed_path': str(processed_path),
                }
            )
            updated_record = update_book_record(
                stable_book_id,
                file_path=str(processed_path),
                source_filename=source_key,
                file_hash=file_hash,
                metadata=updated_metadata,
            ) or record
        finally:
            self._index_write_active.clear()
        self.progress.emit('file_moved_processed', source_key=source_key, book_id=stable_book_id, file_path=str(processed_path), file_hash=file_hash, status='completed')

        qdrant_status = 'disabled'
        if self.qdrant.enabled:
            qdrant_status = 'synced' if self._sync_book_to_qdrant(updated_record, semantic_vector=compute_centroid_embedding(embeddings)) else 'failed'
            if qdrant_status == 'synced':
                self.progress.emit(
                    'qdrant_sync_completed',
                    source_key=source_key,
                    book_id=stable_book_id,
                    file_path=str(processed_path),
                    file_hash=file_hash,
                    title=title,
                    status='synced',
                )

        now = time.time()
        self.store.update_source(
            source_key,
            source_path=str(processed_path),
            status='completed',
            file_hash=file_hash,
            last_processed_hash=file_hash,
            processed_path=str(processed_path),
            failed_path=None,
            format=updated_record.get('format'),
            title=updated_record.get('title'),
            author=self._author_from_record(updated_record),
            chunks_count=int(updated_record.get('chunks_count') or len(chunks)),
            retry_count=0,
            last_error=None,
            next_attempt_at=None,
            last_completed_at=now,
            qdrant_sync_status=qdrant_status,
            qdrant_synced_at=now if qdrant_status == 'synced' else None,
            duplicate_of_book_id=None,
        )
        self.store.record_completed_hash(file_hash, stable_book_id, source_key)
        self._write_manifest(
            source_key,
            {
                'status': 'completed',
                'book_id': stable_book_id,
                'file_hash': file_hash,
                'title': updated_record.get('title'),
                'author': self._author_from_record(updated_record),
                'chunks_count': updated_record.get('chunks_count'),
                'processed_path': str(processed_path),
                'qdrant_sync_status': qdrant_status,
                'updated_at': now,
            },
        )
        return IngestionResult(status='completed', source_key=source_key, book_id=stable_book_id, file_hash=file_hash, processed_path=str(processed_path))

    def _complete_without_reindex(self, file_path: Path, source_key: str, file_hash: str, duplicate: dict[str, Any]) -> IngestionResult:
        book_id = str(duplicate.get('book_id') or '')
        processed_path = self._move_file(file_path, self.settings.dataset_processed_dir, source_key, file_hash=file_hash)
        current_book = self._catalog_book_by_id(book_id)
        if current_book and self._record_belongs_to_source(current_book, source_key):
            metadata = dict(current_book.get('metadata') or {})
            metadata.update({'dataset_processed_path': str(processed_path), 'dataset_source_key': source_key})
            update_book_record(book_id, file_path=str(processed_path), source_filename=source_key, metadata=metadata)
        now = time.time()
        self.store.update_source(
            source_key,
            source_path=str(processed_path),
            stable_book_id=book_id or self._stable_book_id(source_key),
            status='completed',
            file_hash=file_hash,
            last_processed_hash=file_hash,
            processed_path=str(processed_path),
            failed_path=None,
            retry_count=0,
            last_error=None,
            next_attempt_at=None,
            last_completed_at=now,
            qdrant_sync_status='skipped',
            duplicate_of_book_id=book_id or None,
        )
        if book_id:
            self.store.record_completed_hash(file_hash, book_id, source_key)
        self.progress.emit(
            'file_skipped_already_indexed',
            source_key=source_key,
            book_id=book_id,
            file_path=str(processed_path),
            file_hash=file_hash,
            status='completed',
        )
        self.progress.emit('file_moved_processed', source_key=source_key, book_id=book_id, file_path=str(processed_path), file_hash=file_hash, status='completed')
        self._write_manifest(
            source_key,
            {
                'status': 'completed',
                'book_id': book_id,
                'duplicate_of_book_id': book_id,
                'file_hash': file_hash,
                'processed_path': str(processed_path),
                'updated_at': now,
            },
        )
        return IngestionResult(status='skipped', source_key=source_key, book_id=book_id or None, file_hash=file_hash, processed_path=str(processed_path))

    def _handle_failure(self, file_path: Path, source_key: str, file_hash: str | None, error: Exception, error_kind: str) -> IngestionResult:
        current = self.store.get_source(source_key) or {}
        retry_count = int(current.get('retry_count') or 0) + 1
        error_text = str(error)
        should_retry = error_kind == FAILED_TRANSIENT and retry_count < self.settings.ingestion_max_retries
        next_attempt_at = time.time() + min(300, 5 * (2 ** max(0, retry_count - 1))) if should_retry else None

        if should_retry:
            self.store.update_source(
                source_key,
                status='queued',
                retry_count=retry_count,
                last_error=error_text,
                next_attempt_at=next_attempt_at,
            )
            self.progress.emit(
                'file_failed',
                level=logging.WARNING,
                source_key=source_key,
                file_path=str(file_path),
                file_hash=file_hash,
                attempt=retry_count,
                status='retry_scheduled',
                error=error_text,
            )
            return IngestionResult(status='failed', source_key=source_key, file_hash=file_hash, error=error_text)

        failed_path = str(file_path)
        if file_path.exists():
            moved = self._move_file(file_path, self.settings.dataset_failed_dir, source_key, file_hash=file_hash, preserve_name=True)
            failed_path = str(moved)
            self.progress.emit(
                'file_moved_failed',
                level=logging.WARNING,
                source_key=source_key,
                file_path=failed_path,
                file_hash=file_hash,
                status='failed',
                error=error_text,
            )
        self.store.update_source(
            source_key,
            source_path=failed_path,
            status='failed',
            file_hash=file_hash,
            failed_path=failed_path,
            retry_count=retry_count,
            last_error=error_text,
            next_attempt_at=None,
        )
        self.progress.emit(
            'file_failed',
            level=logging.WARNING,
            source_key=source_key,
            file_path=failed_path,
            file_hash=file_hash,
            attempt=retry_count,
            status='failed',
            error=error_text,
        )
        self._write_manifest(
            source_key,
            {
                'status': 'failed',
                'file_hash': file_hash,
                'failed_path': failed_path,
                'error': error_text,
                'retry_count': retry_count,
                'updated_at': time.time(),
            },
        )
        return IngestionResult(status='failed', source_key=source_key, file_hash=file_hash, failed_path=failed_path, error=error_text)

    def _retry_is_delayed(self, record: dict[str, Any] | None) -> bool:
        if not record:
            return False
        next_attempt_at = record.get('next_attempt_at')
        return bool(next_attempt_at and float(next_attempt_at) > time.time())

    def _find_completed_duplicate(self, file_hash: str) -> dict[str, Any] | None:
        from_state = self.store.get_completed_hash(file_hash)
        if from_state:
            return from_state
        for book in load_index(force=True):
            if str(book.get('file_hash') or '') == file_hash:
                return {'book_id': str(book.get('id') or ''), 'source_key': self._record_source_key(book)}
        return None

    def _catalog_book_by_id(self, book_id: str | None) -> dict[str, Any] | None:
        if not book_id:
            return None
        for book in load_index(force=True):
            if str(book.get('id') or '') == str(book_id):
                return book
        return None

    def _record_source_key(self, record: dict[str, Any] | None) -> str:
        metadata = (record or {}).get('metadata') or {}
        if isinstance(metadata, dict):
            return str(metadata.get('dataset_source_key') or '')
        return ''

    def _record_belongs_to_source(self, record: dict[str, Any] | None, source_key: str) -> bool:
        return self._record_source_key(record) == source_key

    def _sync_book_to_qdrant(self, record: dict[str, Any], semantic_vector=None) -> bool:
        try:
            vector = semantic_vector if semantic_vector is not None else get_book_embedding(str(record.get('id')))
            if vector is None:
                return False
            return self.qdrant.upsert_book(
                str(record.get('id')),
                vector.tolist(),
                {
                    'book_id': record.get('id'),
                    'title': record.get('title'),
                    'format': record.get('format'),
                    'author': self._author_from_record(record),
                },
            )
        except Exception:
            return False

    def _stable_book_id(self, source_key: str) -> str:
        digest = hashlib.sha1(source_key.encode('utf-8')).hexdigest()[:16]
        return f'dataset_{digest}'

    def _hash_file(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        with open(file_path, 'rb') as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def _source_key_from_path(self, file_path: Path, prefer_dataset: bool = True) -> str:
        for base_dir in (self.settings.dataset_inbox_dir, self.settings.dataset_root) if prefer_dataset else (self.settings.dataset_root, self.settings.dataset_inbox_dir):
            try:
                relative = file_path.resolve().relative_to(base_dir.resolve())
                return PurePosixPath(relative.as_posix()).as_posix()
            except Exception:
                continue
        return PurePosixPath(file_path.name).as_posix()

    def _destination_path(self, target_root: Path, source_key: str, file_hash: str | None, preserve_name: bool) -> Path:
        relative = Path(PurePosixPath(source_key))
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if preserve_name:
            if destination.exists():
                suffix = f'__{(file_hash or "retry")[:8]}'
                destination = destination.with_name(f'{destination.stem}{suffix}{destination.suffix}')
            return destination
        if destination.exists():
            suffix = f'__{(file_hash or "copy")[:8]}'
            destination = destination.with_name(f'{destination.stem}{suffix}{destination.suffix}')
        return destination

    def _move_file(self, source_path: Path, target_root: Path, source_key: str, file_hash: str | None = None, preserve_name: bool = False) -> Path:
        destination = self._destination_path(target_root, source_key, file_hash, preserve_name=preserve_name)
        shutil.move(str(source_path), str(destination))
        return destination

    def _prepare_manual_reindex_source(self, source_path: Path, source_key: str) -> Path:
        if source_path.resolve().is_relative_to(self.settings.dataset_inbox_dir.resolve()):
            return source_path
        destination = self._destination_path(self.settings.dataset_inbox_dir, source_key, None, preserve_name=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve().is_relative_to(self.settings.dataset_root.resolve()):
            shutil.move(str(source_path), str(destination))
        else:
            shutil.copy2(source_path, destination)
        return destination

    def _write_manifest(self, source_key: str, payload: dict[str, Any]) -> None:
        manifest_name = hashlib.sha1(source_key.encode('utf-8')).hexdigest() + '.json'
        manifest_path = self.settings.dataset_manifests_dir / manifest_name
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, 'w', encoding='utf-8') as file_obj:
            json.dump({'source_key': source_key, **payload}, file_obj, ensure_ascii=False, indent=2)

    @staticmethod
    def _author_from_payload(book_data: dict[str, Any]) -> str:
        metadata = book_data.get('metadata') or {}
        if isinstance(metadata, dict):
            return str(metadata.get('author') or metadata.get('authors') or '').strip()
        return ''

    @staticmethod
    def _author_from_record(record: dict[str, Any]) -> str:
        return get_author(record)
