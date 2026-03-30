from __future__ import annotations

import os
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('NOVELLECT_DATA_DIR', BASE_DIR / 'data'))
UPLOADS_DIR = Path(os.getenv('NOVELLECT_UPLOADS_DIR', BASE_DIR / 'uploads'))
TXT_CACHE_DIR = Path(os.getenv('NOVELLECT_TXT_CACHE_DIR', DATA_DIR / 'txt_cache'))
STORAGE_FILE = Path(os.getenv('NOVELLECT_STORAGE_FILE', DATA_DIR / 'storage.json'))
VECTOR_DB_FILE = Path(os.getenv('NOVELLECT_VECTOR_DB_FILE', DATA_DIR / 'vector_db.npz'))
VECTOR_MANIFEST_FILE = Path(os.getenv('NOVELLECT_VECTOR_MANIFEST_FILE', DATA_DIR / 'vector_manifest.json'))
VECTOR_SEGMENTS_DIR = Path(os.getenv('NOVELLECT_VECTOR_SEGMENTS_DIR', DATA_DIR / 'vector_segments'))
CHUNK_DB_FILE = Path(os.getenv('NOVELLECT_CHUNK_DB_FILE', DATA_DIR / 'chunk_store.sqlite'))
SEARCH_CACHE_FILE = Path(os.getenv('NOVELLECT_SEARCH_CACHE_FILE', DATA_DIR / 'search_cache.pkl'))
RUNTIME_CONFIG_FILE = Path(os.getenv('NOVELLECT_RUNTIME_CONFIG_FILE', BASE_DIR / 'runtime_config.json'))
LEGACY_STORAGE_FILE = BASE_DIR / 'storage.json'
LEGACY_VECTOR_DB_FILE = BASE_DIR / 'vector_db.npz'
LEGACY_SEARCH_CACHE_FILE = BASE_DIR / 'search_cache.pkl'


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    TXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    VECTOR_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    VECTOR_MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    VECTOR_SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    CHUNK_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEARCH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)


def migrate_legacy_runtime_files() -> None:
    if str(os.getenv('NOVELLECT_MIGRATE_LEGACY_RUNTIME', '1')).strip().lower() in {'0', 'false', 'no', 'off'}:
        return
    ensure_runtime_dirs()
    migrations = (
        (LEGACY_STORAGE_FILE, STORAGE_FILE),
        (LEGACY_VECTOR_DB_FILE, VECTOR_DB_FILE),
        (LEGACY_SEARCH_CACHE_FILE, SEARCH_CACHE_FILE),
    )
    for legacy_path, target_path in migrations:
        if target_path.exists() or not legacy_path.exists() or legacy_path == target_path:
            continue
        try:
            shutil.copy2(legacy_path, target_path)
        except Exception:
            continue
