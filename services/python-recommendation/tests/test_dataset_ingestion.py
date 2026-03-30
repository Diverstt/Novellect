import importlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np


def configure_dataset_env(project_root: Path, tmp_dir: Path, *, max_retries: int = 3):
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)

    dataset_root = tmp_dir / 'dataset'
    data_dir = tmp_dir / 'data'
    uploads_dir = tmp_dir / 'uploads'
    txt_cache_dir = tmp_dir / 'txt_cache'
    inbox_dir = dataset_root / 'inbox'
    processed_dir = dataset_root / 'processed'
    failed_dir = dataset_root / 'failed'
    manifests_dir = dataset_root / 'manifests'
    for directory in (dataset_root, data_dir, uploads_dir, txt_cache_dir, inbox_dir, processed_dir, failed_dir, manifests_dir):
        directory.mkdir(parents=True, exist_ok=True)

    os.environ['NOVELLECT_DATA_DIR'] = str(data_dir)
    os.environ['NOVELLECT_STORAGE_FILE'] = str(data_dir / 'storage.json')
    os.environ['NOVELLECT_VECTOR_DB_FILE'] = str(data_dir / 'vector_db.npz')
    os.environ['NOVELLECT_VECTOR_MANIFEST_FILE'] = str(data_dir / 'vector_manifest.json')
    os.environ['NOVELLECT_VECTOR_SEGMENTS_DIR'] = str(data_dir / 'vector_segments')
    os.environ['NOVELLECT_CHUNK_DB_FILE'] = str(data_dir / 'chunk_store.sqlite')
    os.environ['NOVELLECT_SEARCH_CACHE_FILE'] = str(data_dir / 'search_cache.pkl')
    os.environ['NOVELLECT_TXT_CACHE_DIR'] = str(txt_cache_dir)
    os.environ['NOVELLECT_UPLOADS_DIR'] = str(uploads_dir)
    os.environ['NOVELLECT_MIGRATE_LEGACY_RUNTIME'] = '0'
    os.environ['NOVELLECT_PROFILES_FILE'] = str(data_dir / 'profiles.json')
    os.environ['NOVELLECT_INTERACTIONS_FILE'] = str(data_dir / 'events.json')
    os.environ['NOVELLECT_SESSIONS_FILE'] = str(data_dir / 'sessions.json')
    os.environ['NOVELLECT_SEARCH_MODE'] = 'lite'
    os.environ['NOVELLECT_USE_STUB_LLM'] = '1'
    os.environ['NOVELLECT_LLM_MODEL_ID'] = 'stub'
    os.environ['NOVELLECT_QDRANT_URL'] = ''
    os.environ['INGESTION_ENABLED'] = '1'
    os.environ['INGESTION_SCAN_ON_START'] = '0'
    os.environ['INGESTION_WATCH_MODE'] = '0'
    os.environ['INGESTION_POLL_INTERVAL_SEC'] = '5'
    os.environ['INGESTION_STATE_DB_PATH'] = str(data_dir / 'ingestion_state.sqlite')
    os.environ['INGESTION_PROGRESS_LOG_PATH'] = str(data_dir / 'ingestion_progress.jsonl')
    os.environ['INGESTION_EMBEDDING_BATCH_SIZE'] = '8'
    os.environ['INGESTION_MAX_RETRIES'] = str(max_retries)
    os.environ['INGESTION_WORKER_CONCURRENCY'] = '1'
    os.environ['DATASET_ROOT'] = str(dataset_root)
    os.environ['DATASET_INBOX_DIR'] = str(inbox_dir)
    os.environ['DATASET_PROCESSED_DIR'] = str(processed_dir)
    os.environ['DATASET_FAILED_DIR'] = str(failed_dir)
    os.environ['DATASET_MANIFESTS_DIR'] = str(manifests_dir)

    module_names = [
        'service_paths',
        'runtime_config',
        'storage',
        'converter',
        'search_engine',
        'catalog_utils',
        'book_metadata',
        'service_settings',
        'dataset_ingestion',
        'recommendation_service',
    ]
    modules = {}
    for name in module_names:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)
    return modules


def make_service(project_root: Path, tmp_dir: Path, *, max_retries: int = 3):
    modules = configure_dataset_env(project_root, tmp_dir, max_retries=max_retries)
    return modules['recommendation_service'].RecommendationService()


def write_inbox_file(tmp_dir: Path, relative_path: str, content: str):
    inbox_dir = tmp_dir / 'dataset' / 'inbox'
    file_path = inbox_dir / Path(relative_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding='utf-8')
    return file_path


def fetch_source_state(tmp_dir: Path, source_key: str):
    conn = sqlite3.connect(tmp_dir / 'data' / 'ingestion_state.sqlite')
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT * FROM ingestion_sources WHERE source_key = ?', (source_key,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def test_startup_scan_indexes_books_and_moves_to_processed():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        service = make_service(project_root, tmp_dir)
        inbox_path = write_inbox_file(tmp_dir, 'batch/book-one.txt', 'castle mystery secret investigation dark manor and hidden room')

        summary = service.run_ingestion_scan_once()

        assert summary['processed'] == 1
        assert not inbox_path.exists()
        books = service.list_books()
        assert len(books) == 1
        processed_path = Path(books[0]['file_path'])
        assert processed_path.exists()
        assert 'processed' in processed_path.parts
        state = fetch_source_state(tmp_dir, 'batch/book-one.txt')
        assert state is not None
        assert state['status'] == 'completed'
        assert state['processed_path'] == str(processed_path)


def test_restart_reuses_existing_index_without_reprocessing():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        service = make_service(project_root, tmp_dir)
        write_inbox_file(tmp_dir, 'library/reuse.txt', 'warm road friendship hopeful travel and quiet courage across the valley')

        first = service.run_ingestion_scan_once()
        first_book = service.list_books()[0]
        vector_path = Path(os.environ['NOVELLECT_VECTOR_DB_FILE'])
        initial_mtime = vector_path.stat().st_mtime_ns

        service = make_service(project_root, tmp_dir)
        second = service.run_ingestion_scan_once()
        second_book = service.list_books()[0]

        assert first['processed'] == 1
        assert second['processed'] == 0
        assert second['skipped'] == 0
        assert second_book['id'] == first_book['id']
        assert second_book['added_date'] == first_book['added_date']
        assert vector_path.stat().st_mtime_ns == initial_mtime


def test_changed_file_reindexes_same_book_id():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        service = make_service(project_root, tmp_dir)
        write_inbox_file(tmp_dir, 'updates/story.txt', 'old version with sleepy town and slow evening around the station')
        service.run_ingestion_scan_once()
        first_book = service.list_books()[0]
        first_hash = first_book['file_hash']

        write_inbox_file(tmp_dir, 'updates/story.txt', 'new version with stormy station chase urgent escape and mystery letter')
        service.run_ingestion_scan_once()
        second_book = service.list_books()[0]

        assert second_book['id'] == first_book['id']
        assert second_book['file_hash'] != first_hash
        assert 'stormy station chase' in Path(second_book['file_path']).read_text(encoding='utf-8')
        state = fetch_source_state(tmp_dir, 'updates/story.txt')
        assert state is not None
        assert state['status'] == 'completed'
        assert state['last_processed_hash'] == second_book['file_hash']


def test_failed_file_moves_to_failed_bucket():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        service = make_service(project_root, tmp_dir, max_retries=1)
        inbox_path = write_inbox_file(tmp_dir, 'broken/empty.txt', 'short')

        summary = service.run_ingestion_scan_once()

        assert summary['failed'] == 1
        assert not inbox_path.exists()
        state = fetch_source_state(tmp_dir, 'broken/empty.txt')
        assert state is not None
        assert state['status'] == 'failed'
        assert state['failed_path']
        assert Path(state['failed_path']).exists()
        assert 'failed' in Path(state['failed_path']).parts


def test_recommendations_work_with_dataset_indexed_books():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        service = make_service(project_root, tmp_dir)
        write_inbox_file(tmp_dir, 'feed/book-a.txt', 'dark castle mystery secret passage hidden portrait midnight rain')
        write_inbox_file(tmp_dir, 'feed/book-b.txt', 'sunny road friendship travel hope reunion and quiet healing journey')
        service.run_ingestion_scan_once()

        payload = service.get_recommendations(
            'user-dataset',
            mode='similar',
            limit=2,
            session_id='s-dataset',
            query='dark castle mystery',
        )

        assert payload['items']
        indexed_ids = {book['id'] for book in service.list_books()}
        assert payload['items'][0]['book_id'] in indexed_ids


def test_segmented_vector_storage_keeps_chunk_text_out_of_segment_metadata():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        modules = configure_dataset_env(project_root, tmp_dir)
        search_engine = modules['search_engine']

        file_path = tmp_dir / 'sample.txt'
        file_path.write_text('dark castle mystery hidden portrait midnight rain and cold corridor', encoding='utf-8')
        payload = {
            'title': 'Segment smoke',
            'content': file_path.read_text(encoding='utf-8'),
            'format': 'txt',
            'file_path': str(file_path),
            'source_filename': file_path.name,
            'metadata': {'author': 'Segment Author'},
            'file_hash': 'segment-smoke',
        }

        assert search_engine.add_to_index(payload, file_id='segment-smoke') is not None

        manifest_path = Path(os.environ['NOVELLECT_VECTOR_MANIFEST_FILE'])
        marker_path = Path(os.environ['NOVELLECT_VECTOR_DB_FILE'])
        chunk_db_path = Path(os.environ['NOVELLECT_CHUNK_DB_FILE'])
        segment_dir = Path(os.environ['NOVELLECT_VECTOR_SEGMENTS_DIR'])

        assert manifest_path.exists()
        assert marker_path.exists()
        assert chunk_db_path.exists()
        segment_files = list(segment_dir.glob('*.npz'))
        assert segment_files

        raw_segment = np.load(segment_files[0], allow_pickle=True)
        metadata = json.loads(raw_segment['metadata'].item())
        assert metadata
        assert 'chunk' not in metadata[0]

        _embeddings, loaded_metadata = search_engine.load_vector_db(sync_with_index=True, force_reload=True)
        assert loaded_metadata
        assert loaded_metadata[0].get('chunk')
