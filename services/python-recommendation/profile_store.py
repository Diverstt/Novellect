from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from service_settings import ServiceSettings

logger = logging.getLogger(__name__)
PROFILE_TABLE = 'recommendation_profiles'


def _import_psycopg():
    import psycopg

    return psycopg


class JSONProfileStore:
    backend = 'json'

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, 'r', encoding='utf-8') as file_obj:
                payload = json.load(file_obj)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _save(self, payload: dict[str, Any]):
        with open(self.path, 'w', encoding='utf-8') as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)

    def get(self, key: str):
        with self._lock:
            return self._load().get(key)

    def upsert(self, key: str, value: Any):
        with self._lock:
            state = self._load()
            state[key] = value
            self._save(state)
            return value


class PostgresProfileStore:
    backend = 'postgres'

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        timeout_sec: int = 5,
        fallback: JSONProfileStore | None = None,
    ):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.database = database
        self.timeout_sec = max(1, int(timeout_sec))
        self.fallback = fallback
        self._lock = threading.RLock()
        self._schema_ready = False

    def _connect(self):
        psycopg = _import_psycopg()
        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.database,
            connect_timeout=self.timeout_sec,
            autocommit=True,
        )

    def _fallback_get(self, key: str):
        return self.fallback.get(key) if self.fallback else None

    @staticmethod
    def _parse_json_payload(raw: Any) -> dict[str, Any]:
        try:
            payload = json.loads(str(raw or '{}'))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _legacy_get(self, key: str):
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(onboarding_json::text, '{}') FROM user_profiles WHERE user_id = %s LIMIT 1",
                    (key,),
                )
                row = cur.fetchone()
                if row:
                    onboarding = self._parse_json_payload(row[0])
                    if onboarding:
                        return {'user_id': key, 'onboarding': onboarding}
                cur.execute(
                    "SELECT COALESCE(payload::text, '{}') FROM onboarding_state WHERE user_id = %s LIMIT 1",
                    (key,),
                )
                row = cur.fetchone()
                if row:
                    onboarding = self._parse_json_payload(row[0])
                    if onboarding:
                        return {'user_id': key, 'onboarding': onboarding}
        except Exception as exc:
            logger.warning('legacy onboarding bootstrap failed: %s', exc)
        return None

    def _ensure_schema(self, conn):
        if self._schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {PROFILE_TABLE} (
                    user_id TEXT PRIMARY KEY,
                    profile_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                )
                """
            )
        self._schema_ready = True

    def get(self, key: str):
        with self._lock:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    self._ensure_schema(conn)
                    cur.execute(
                        f"SELECT COALESCE(profile_json::text, '{{}}') FROM {PROFILE_TABLE} WHERE user_id = %s LIMIT 1",
                        (key,),
                    )
                    row = cur.fetchone()
                if not row:
                    return self._legacy_get(key) or self._fallback_get(key)
                payload = self._parse_json_payload(row[0])
                if isinstance(payload, dict):
                    if self.fallback:
                        self.fallback.upsert(key, payload)
                    return payload
            except Exception as exc:
                logger.warning('postgres profile store read failed, using fallback: %s', exc)
            return self._fallback_get(key)

    def upsert(self, key: str, value: Any):
        now = int(time.time())
        payload = value if isinstance(value, dict) else {}
        with self._lock:
            try:
                created_at = now
                with self._connect() as conn, conn.cursor() as cur:
                    self._ensure_schema(conn)
                    cur.execute(
                        f"SELECT created_at FROM {PROFILE_TABLE} WHERE user_id = %s LIMIT 1",
                        (key,),
                    )
                    existing = cur.fetchone()
                    if existing:
                        created_at = int(existing[0] or now)
                    cur.execute(
                        f"""
                        INSERT INTO {PROFILE_TABLE} (user_id, profile_json, created_at, updated_at)
                        VALUES (%s, %s::jsonb, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE
                        SET profile_json = EXCLUDED.profile_json,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (key, json.dumps(payload, ensure_ascii=False), created_at, now),
                    )
                if self.fallback:
                    self.fallback.upsert(key, payload)
                return payload
            except Exception as exc:
                logger.warning('postgres profile store write failed, using fallback: %s', exc)
                if self.fallback:
                    return self.fallback.upsert(key, payload)
                return payload


def build_profile_store(settings: ServiceSettings):
    json_store = JSONProfileStore(settings.profiles_file)
    backend = str(settings.profile_store_backend or 'auto').strip().lower()
    postgres_configured = all([settings.postgres_host, settings.postgres_user, settings.postgres_database])
    if backend == 'json':
        return json_store
    if backend in {'auto', 'postgres'} and postgres_configured:
        return PostgresProfileStore(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            database=settings.postgres_database,
            timeout_sec=settings.postgres_timeout_sec,
            fallback=json_store,
        )
    return json_store
