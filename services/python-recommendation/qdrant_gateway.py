from __future__ import annotations

import uuid

import requests

from catalog_utils import feature_key_order


class QdrantGateway:
    def __init__(self, base_url: str = '', timeout_sec: int = 5, book_collection: str = 'book_semantic_vectors', user_collection: str = 'user_taste_vectors'):
        self.base_url = base_url.rstrip('/')
        self.timeout_sec = timeout_sec
        self.book_collection = book_collection
        self.user_collection = user_collection
        self.user_vector_size = len(feature_key_order())

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    @staticmethod
    def _point_id(raw_id: str) -> str:
        text = str(raw_id or '').strip()
        if not text:
            return str(uuid.uuid4())
        try:
            return str(uuid.UUID(text))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, text))

    def _request(self, method: str, path: str, payload: dict | None = None):
        if not self.enabled:
            return None
        try:
            response = requests.request(method, self.base_url + path, json=payload, timeout=self.timeout_sec)
            response.raise_for_status()
            return response.json() if response.content else None
        except Exception:
            return None

    @staticmethod
    def _declared_vector_size(collection_info: dict | None) -> int | None:
        result = (collection_info or {}).get('result') or {}
        config = (result.get('config') or {}).get('params') or {}
        vectors = config.get('vectors') or {}
        if isinstance(vectors, dict):
            size = vectors.get('size')
            if size is not None:
                try:
                    return int(size)
                except (TypeError, ValueError):
                    return None
        return None

    def _ensure_collection(self, collection_name: str, vector_size: int):
        if not self.enabled or vector_size <= 0:
            return False
        existing = self._request('GET', f'/collections/{collection_name}')
        current_size = self._declared_vector_size(existing)
        if current_size == int(vector_size):
            return True
        if current_size is not None and current_size != int(vector_size):
            self._request('DELETE', f'/collections/{collection_name}')
        schema = {'vectors': {'size': int(vector_size), 'distance': 'Cosine'}}
        return self._request('PUT', f'/collections/{collection_name}', schema) is not None

    def ensure_collections(self, book_vector_size: int | None = None):
        if not self.enabled:
            return False
        if book_vector_size is not None:
            self._ensure_collection(self.book_collection, int(book_vector_size))
        self._ensure_collection(self.user_collection, self.user_vector_size)
        return True

    def upsert_book(self, book_id: str, vector: list[float], payload: dict | None = None):
        if not self.enabled:
            return False
        if not vector:
            return False
        self.ensure_collections(book_vector_size=len(vector))
        point_id = self._point_id(book_id)
        return self._request('PUT', f'/collections/{self.book_collection}/points', {'points': [{'id': point_id, 'vector': vector, 'payload': payload or {}}]}) is not None

    def upsert_user_vector(self, user_id: str, vector: list[float], payload: dict | None = None):
        if not self.enabled:
            return False
        if not vector:
            return False
        self.ensure_collections()
        point_id = self._point_id(user_id)
        return self._request('PUT', f'/collections/{self.user_collection}/points', {'points': [{'id': point_id, 'vector': vector, 'payload': payload or {}}]}) is not None

    def search_books(self, vector: list[float], limit: int = 20):
        if not self.enabled:
            return []
        if not vector:
            return []
        self.ensure_collections(book_vector_size=len(vector))
        result = self._request('POST', f'/collections/{self.book_collection}/points/search', {'vector': vector, 'limit': int(limit), 'with_payload': True}) or {}
        return result.get('result') or []
