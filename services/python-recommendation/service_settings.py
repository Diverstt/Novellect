from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from service_paths import DATA_DIR


@dataclass(slots=True)
class ServiceSettings:
    profiles_file: Path
    events_file: Path
    sessions_file: Path
    qdrant_url: str
    qdrant_timeout_sec: int
    qdrant_book_collection: str
    qdrant_user_collection: str
    cache_ttl_sec: int
    profile_store_backend: str
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_database: str
    postgres_timeout_sec: int
    dataset_root: Path = Path('/dataset')
    dataset_inbox_dir: Path = Path('/dataset/inbox')
    dataset_processed_dir: Path = Path('/dataset/processed')
    dataset_failed_dir: Path = Path('/dataset/failed')
    dataset_manifests_dir: Path = Path('/dataset/manifests')
    ingestion_enabled: bool = False
    ingestion_scan_on_start: bool = False
    ingestion_watch_mode: bool = False
    ingestion_poll_interval_sec: int = 30
    ingestion_state_db_path: Path = DATA_DIR / 'ingestion_state.sqlite'
    ingestion_progress_log_path: Path = DATA_DIR / 'ingestion_progress.jsonl'
    ingestion_embedding_batch_size: int = 32
    ingestion_max_retries: int = 3
    ingestion_worker_concurrency: int = 1
    ingestion_pause_on_foreground: bool = True
    online_search_max_specs: int = 2
    online_search_candidate_factor: int = 3
    online_search_min_candidates: int = 12
    online_search_qdrant_enabled: bool = True
    online_search_qdrant_limit_factor: int = 3
    online_search_warm_sync: bool = True


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = str(os.getenv(name, default)).strip()
    try:
        return max(minimum, int(raw))
    except Exception:
        return max(minimum, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, '1' if default else '0')).strip().lower()
    return raw not in {'0', 'false', 'no', 'off'}


def load_service_settings() -> ServiceSettings:
    data_dir = DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    backend = str(os.getenv('NOVELLECT_PROFILE_STORE_BACKEND', 'auto')).strip().lower() or 'auto'
    dataset_root = Path(os.getenv('DATASET_ROOT', '/dataset'))
    return ServiceSettings(
        profiles_file=Path(os.getenv('NOVELLECT_PROFILES_FILE', data_dir / 'user_profiles.json')),
        events_file=Path(os.getenv('NOVELLECT_INTERACTIONS_FILE', data_dir / 'interaction_events.json')),
        sessions_file=Path(os.getenv('NOVELLECT_SESSIONS_FILE', data_dir / 'session_state.json')),
        qdrant_url=os.getenv('NOVELLECT_QDRANT_URL', '').rstrip('/'),
        qdrant_timeout_sec=_env_int('NOVELLECT_QDRANT_TIMEOUT_SEC', 5),
        qdrant_book_collection=os.getenv('NOVELLECT_QDRANT_BOOK_COLLECTION', 'book_semantic_vectors'),
        qdrant_user_collection=os.getenv('NOVELLECT_QDRANT_USER_COLLECTION', 'user_taste_vectors'),
        cache_ttl_sec=_env_int('NOVELLECT_CACHE_TTL_SEC', 300),
        profile_store_backend=backend,
        postgres_host=str(os.getenv('POSTGRES_HOST', '')).strip(),
        postgres_port=_env_int('POSTGRES_PORT', 5432),
        postgres_user=str(os.getenv('POSTGRES_USER', '')).strip(),
        postgres_password=str(os.getenv('POSTGRES_PASSWORD', '')).strip(),
        postgres_database=str(os.getenv('POSTGRES_DB', '')).strip(),
        postgres_timeout_sec=_env_int('POSTGRES_TIMEOUT_SEC', 5),
        dataset_root=dataset_root,
        dataset_inbox_dir=Path(os.getenv('DATASET_INBOX_DIR', dataset_root / 'inbox')),
        dataset_processed_dir=Path(os.getenv('DATASET_PROCESSED_DIR', dataset_root / 'processed')),
        dataset_failed_dir=Path(os.getenv('DATASET_FAILED_DIR', dataset_root / 'failed')),
        dataset_manifests_dir=Path(os.getenv('DATASET_MANIFESTS_DIR', dataset_root / 'manifests')),
        ingestion_enabled=_env_bool('INGESTION_ENABLED', False),
        ingestion_scan_on_start=_env_bool('INGESTION_SCAN_ON_START', True),
        ingestion_watch_mode=_env_bool('INGESTION_WATCH_MODE', True),
        ingestion_poll_interval_sec=_env_int('INGESTION_POLL_INTERVAL_SEC', 30, minimum=5),
        ingestion_state_db_path=Path(os.getenv('INGESTION_STATE_DB_PATH', DATA_DIR / 'ingestion_state.sqlite')),
        ingestion_progress_log_path=Path(os.getenv('INGESTION_PROGRESS_LOG_PATH', DATA_DIR / 'ingestion_progress.jsonl')),
        ingestion_embedding_batch_size=_env_int('INGESTION_EMBEDDING_BATCH_SIZE', 32),
        ingestion_max_retries=_env_int('INGESTION_MAX_RETRIES', 3),
        ingestion_worker_concurrency=_env_int('INGESTION_WORKER_CONCURRENCY', 1),
        ingestion_pause_on_foreground=_env_bool('INGESTION_PAUSE_ON_FOREGROUND', True),
        online_search_max_specs=_env_int('ONLINE_SEARCH_MAX_SPECS', 2),
        online_search_candidate_factor=_env_int('ONLINE_SEARCH_CANDIDATE_FACTOR', 3),
        online_search_min_candidates=_env_int('ONLINE_SEARCH_MIN_CANDIDATES', 12),
        online_search_qdrant_enabled=_env_bool('ONLINE_SEARCH_QDRANT_ENABLED', True),
        online_search_qdrant_limit_factor=_env_int('ONLINE_SEARCH_QDRANT_LIMIT_FACTOR', 3),
        online_search_warm_sync=_env_bool('ONLINE_SEARCH_WARM_SYNC', True),
    )
