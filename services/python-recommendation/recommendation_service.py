from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agents import AgentOrchestrator, QueryAnalyzerAgent
from ai_layer import generate_card_explanation, query_match_score, query_reason_candidates, understand_query
from book_metadata import get_author, get_cover_url, get_genres, get_hybrid_tags, get_moods, get_summary, normalize_label
from cache_backend import TTLCache
from catalog_utils import book_feature_map, book_index_by_id, feature_priorities_from_flattened, feature_richness, feature_vector
from dataset_ingestion import DatasetIngestionCoordinator
from ingestion_service import ingest_text
from profile_store import JSONProfileStore, build_profile_store
from qdrant_gateway import QdrantGateway
from search_engine import (
    find_title_matches,
    get_book_embedding,
    get_query_analyzer,
    get_query_embedding,
    load_index,
    rebuild_semantic_passports,
    search_hybrid,
    tokenize_smart,
    update_last_opened,
    warm_runtime,
)
from service_settings import load_service_settings
from taste_profile import apply_onboarding, build_long_term_taste, combined_taste, empty_profile, normalize_profile, profile_needs_bootstrap, recommendation_priorities, score_book_for_profile, taste_profile_summary, update_profile_from_interaction

LOGGER = logging.getLogger(__name__)


class RecommendationService:
    def __init__(self):
        settings = load_service_settings()
        self.settings = settings
        self.profile_store = build_profile_store(settings)
        self.event_store = JSONProfileStore(settings.events_file)
        self.session_store = JSONProfileStore(settings.sessions_file)
        self.cache = TTLCache()
        self.qdrant = QdrantGateway(settings.qdrant_url, settings.qdrant_timeout_sec, settings.qdrant_book_collection, settings.qdrant_user_collection)
        self.ingestion = DatasetIngestionCoordinator(settings, self.qdrant)
        self._agent = AgentOrchestrator()
        self._query_profile_analyzer = QueryAnalyzerAgent()
        self._warm_thread: threading.Thread | None = None

    def list_books(self):
        return load_index(force=True)

    def get_book(self, book_id: str):
        return book_index_by_id().get(book_id)

    def health(self):
        return {'status': 'ok', 'books_indexed': len(self.list_books()), 'qdrant_enabled': self.qdrant.enabled, 'profile_store_backend': getattr(self.profile_store, 'backend', 'unknown')}

    def start_background_tasks(self):
        if self.settings.online_search_warm_sync:
            started = time.perf_counter()
            warm_runtime()
            LOGGER.info('search_runtime_warmed mode=sync duration_ms=%s', int((time.perf_counter() - started) * 1000))
        elif self._warm_thread is None or not self._warm_thread.is_alive():
            self._warm_thread = threading.Thread(target=warm_runtime, name='search-runtime-warm', daemon=True)
            self._warm_thread.start()
        return self.ingestion.start()

    def stop_background_tasks(self):
        self.ingestion.stop()

    def run_ingestion_scan_once(self):
        return self.ingestion.scan_once()

    def retry_failed_ingestion(self):
        return {'retried': self.ingestion.retry_failed()}

    def rebuild_semantic_catalog(self, limit: int | None = None):
        result = rebuild_semantic_passports(limit=limit)
        self.cache.clear()
        return result

    def reindex_dataset_path(self, file_path: str):
        result = self.ingestion.reindex_path(file_path)
        return {
            'status': result.status,
            'source_key': result.source_key,
            'book_id': result.book_id,
            'file_hash': result.file_hash,
            'processed_path': result.processed_path,
            'failed_path': result.failed_path,
            'error': result.error,
        }

    @contextmanager
    def _foreground_request(self):
        self.ingestion.begin_foreground_work()
        try:
            self.ingestion.wait_for_index_write_idle()
            yield
        finally:
            self.ingestion.end_foreground_work()

    def ingest_text(self, title: str, content: str, metadata: dict | None = None, file_id: str | None = None):
        result = ingest_text(title, content, metadata, file_id)
        record = result.get('record')
        if record:
            self._sync_book_to_qdrant(record)
        return result

    def run_search(self, query: str):
        with self._foreground_request():
            started = time.perf_counter()
            payload = self._agent.process_query(query)
            LOGGER.info(
                'search_request_completed query=%r query_type=%s duration_ms=%s',
                (query or '')[:120],
                payload.get('query_type', payload.get('type', 'unknown')),
                int((time.perf_counter() - started) * 1000),
            )
            return payload

    def _ensure_profile(self, user_id: str):
        profile = self.profile_store.get(user_id)
        if profile:
            normalized = normalize_profile(profile, user_id)
            if profile_needs_bootstrap(profile) and normalized.get('onboarding'):
                normalized = apply_onboarding(normalized, normalized.get('onboarding') or {})
            if normalized != profile:
                self.profile_store.upsert(user_id, normalized)
            return normalized
        profile = empty_profile(user_id)
        self.profile_store.upsert(user_id, profile)
        return profile

    def get_profile(self, user_id: str, session_id: str | None = None):
        return taste_profile_summary(self._ensure_profile(user_id), session_id)

    def apply_onboarding(self, user_id: str, payload: dict[str, Any]):
        profile = apply_onboarding(self._ensure_profile(user_id), payload)
        self.profile_store.upsert(user_id, profile)
        self.cache.delete_prefix(f'feed:{user_id}:')
        self._sync_user_profile_to_qdrant(profile)
        return taste_profile_summary(profile)

    def ingest_interaction(self, user_id: str, book_id: str, action: str, session_id: str | None = None, source: str = 'api', metadata: dict | None = None):
        book = self.get_book(book_id)
        if book is None:
            raise KeyError(f'Book {book_id} not found in catalog.')
        profile = update_profile_from_interaction(self._ensure_profile(user_id), book, action, session_id, metadata)
        self.profile_store.upsert(user_id, profile)
        events = self.event_store.get(user_id) or {'events': []}
        events.setdefault('events', []).append({'book_id': book_id, 'action': action, 'source': source, 'metadata': metadata or {}, 'session_id': session_id, 'timestamp': time.time()})
        self.event_store.upsert(user_id, events)
        self.cache.delete_prefix(f'feed:{user_id}:')
        self._sync_user_profile_to_qdrant(profile)
        return {'status': 'accepted', 'user_id': user_id, 'book_id': book_id, 'action': action, 'profile': taste_profile_summary(profile, session_id)}

    def _book_text(self, book: dict[str, Any]) -> str:
        for key in ('content',):
            value = book.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for path_key in ('file_path', 'cache_path'):
            path = book.get(path_key)
            if not path:
                continue
            try:
                if os.path.exists(path):
                    return Path(path).read_text(encoding='utf-8', errors='ignore').strip()
            except Exception:
                continue
        return ''

    def _summary_for_book(self, book: dict[str, Any]) -> str:
        return get_summary(book)

    def _preview_excerpt(self, book: dict[str, Any], query: str | None = None) -> str:
        text = self._book_text(book)
        if not text:
            return self._summary_for_book(book)
        compact = ' '.join(text.split())
        if not query:
            return compact[:340].rstrip() + ('...' if len(compact) > 340 else '')
        lowered = compact.lower().replace('ё', 'е')
        for token in tokenize_smart(query):
            pos = lowered.find(token)
            if pos != -1:
                start = max(0, pos - 120)
                end = min(len(compact), pos + 220)
                if start > 0:
                    left_boundary = compact.rfind(' ', 0, start)
                    if left_boundary != -1:
                        start = left_boundary + 1
                if end < len(compact):
                    right_boundary = compact.find(' ', end)
                    if right_boundary != -1:
                        end = right_boundary
                excerpt = compact[start:end].strip()
                if start > 0:
                    excerpt = '...' + excerpt
                if end < len(compact):
                    excerpt += '...'
                return re.sub(r'^\.\.\.(\S)', r'... \1', excerpt)
        return compact[:340].rstrip() + ('...' if len(compact) > 340 else '')

    def get_book_preview(self, book_id: str):
        book = self.get_book(book_id)
        if book is None:
            return None
        return {
            'book_id': str(book.get('id')),
            'title': book.get('title'),
            'author': get_author(book),
            'format': book.get('format', 'unknown'),
            'short_summary': self._summary_for_book(book),
            'preview_excerpt': self._preview_excerpt(book),
            'cover_url': get_cover_url(book),
            'genres': get_genres(book),
            'moods': get_moods(book),
            'read_url': f"/api/v1/books/{book_id}/content",
        }

    def get_book_content(self, book_id: str):
        book = self.get_book(book_id)
        if book is None:
            return None
        update_last_opened(book_id)
        content = self._book_text(book)
        return {
            'book_id': str(book.get('id')),
            'title': book.get('title'),
            'author': get_author(book),
            'format': book.get('format', 'unknown'),
            'summary': self._summary_for_book(book),
            'content': content,
            'content_length': len(content),
            'cover_url': get_cover_url(book),
            'genres': get_genres(book),
            'moods': get_moods(book),
            'preview_excerpt': self._preview_excerpt(book),
            'read_url': f"/api/v1/books/{book_id}/content",
        }

    def _cold_start_books(self, limit: int):
        return sorted(load_index(force=True), key=lambda book: (feature_richness(book), book.get('open_count', 0)), reverse=True)[:limit]

    def _query_from_profile(self, profile: dict[str, Any], mode: str, seed_book: dict | None = None, session_id: str | None = None) -> str:
        priorities = recommendation_priorities(profile, session_id)
        tokens = []
        for _category, values in priorities.items():
            tokens.extend(name for name, _score in values[:2])
        tokens.extend(profile.get('preferred_genres') or [])
        tokens.extend(profile.get('preferred_authors') or [])
        if seed_book:
            tokens.insert(0, seed_book.get('title', ''))
            tokens.extend(get_genres(seed_book)[:2])
        if mode == 'risk':
            tokens.append('необычная книга')
        elif mode == 'new':
            tokens.append('что-то новое')
        if not tokens:
            onboarding = profile.get('onboarding') or {}
            tokens.extend(onboarding.get('favorite_books') or [])
            tokens.extend(onboarding.get('favorite_moods') or [])
            tokens.extend(onboarding.get('favorite_genres') or [])
            tokens.extend(onboarding.get('favorite_authors') or [])
        result, seen = [], set()
        for token in tokens:
            cleaned = str(token).strip()
            key = normalize_label(cleaned)
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return ' '.join(result[:10]).strip() or 'интересная книга'

    def _profile_hint_tokens(self, profile: dict[str, Any], session_id: str | None = None, limit: int = 4) -> list[str]:
        priorities = recommendation_priorities(profile, session_id)
        tokens: list[str] = []
        for category in ('mood', 'atmosphere', 'plot', 'tone', 'style'):
            tokens.extend(name for name, _score in (priorities.get(category) or [])[:1])
        tokens.extend((profile.get('preferred_genres') or [])[:2])
        tokens.extend((profile.get('preferred_authors') or [])[:1])
        unique: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            cleaned = str(token).strip()
            key = normalize_label(cleaned)
            if cleaned and key and key not in seen:
                seen.add(key)
                unique.append(cleaned)
        return unique[:limit]

    @staticmethod
    def _dedupe_tokens(tokens: list[str], limit: int = 12) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            cleaned = str(token).strip()
            key = normalize_label(cleaned)
            if cleaned and key and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return result[:limit]

    @staticmethod
    def _normalize_context_value(raw_value: str | None) -> str:
        cleaned = str(raw_value or '').strip().lower()
        if cleaned in {'profile', 'query'}:
            return cleaned
        return ''

    @classmethod
    def _resolve_contexts(cls, recommendation_context: str | None, search_context: str | None, query: str | None) -> tuple[str, str, str]:
        resolved_search_context = cls._normalize_context_value(search_context)
        resolved_recommendation_context = cls._normalize_context_value(recommendation_context)

        if not resolved_search_context and resolved_recommendation_context:
            resolved_search_context = resolved_recommendation_context
        if not resolved_recommendation_context and resolved_search_context:
            resolved_recommendation_context = resolved_search_context

        if not resolved_search_context and not resolved_recommendation_context:
            default_context = 'query' if query and query.strip() else 'profile'
            resolved_search_context = default_context
            resolved_recommendation_context = default_context

        if resolved_search_context and resolved_recommendation_context and resolved_search_context != resolved_recommendation_context:
            resolved_recommendation_context = resolved_search_context

        if resolved_search_context == 'query':
            request_mode = 'query_only'
        elif query and query.strip():
            request_mode = 'profile_with_query'
        else:
            request_mode = 'profile_only'

        return resolved_search_context, resolved_recommendation_context, request_mode

    def _query_retrieval_text(self, query: str, query_understanding: dict[str, Any] | None) -> str:
        parts = [str(query or '').strip()]
        if query_understanding:
            for key in ('genres', 'themes', 'moods', 'tone', 'comparables', 'constraints'):
                parts.extend(str(item) for item in (query_understanding.get(key) or [])[:3])
        return ' '.join(self._dedupe_tokens(parts, limit=12)).strip()

    def _build_live_query(self, profile: dict[str, Any], mode: str, query: str | None, seed_book: dict | None, session_id: str | None, query_understanding: dict[str, Any] | None) -> str:
        if query and query.strip():
            raw_query = str(query).strip()
            raw_analysis = self._query_profile_analyzer.analyze(raw_query)
            raw_type = str(raw_analysis.get('type') or 'recommendation')
            if raw_type in {'title', 'entity', 'specific', 'scene'} or bool(raw_analysis.get('require_exact')):
                return raw_query
            return self._query_retrieval_text(raw_query, query_understanding)
        return self._query_from_profile(profile, mode, seed_book, session_id)

    def _search_profile(self, original_query: str, search_text: str, recommendation_context: str = 'profile') -> dict[str, Any]:
        analysis = get_query_analyzer().analyze_query(search_text)
        priorities = analysis.get('priorities') or {}
        query_type = 'recommendation'
        title_like = False
        require_exact = False
        keywords = tokenize_smart(search_text)[:8]
        has_features = {
            'mood': bool(priorities.get('mood')),
            'style': bool(priorities.get('style')),
            'plot': bool(priorities.get('plot')),
            'atmosphere': bool(priorities.get('atmosphere')),
            'tone': bool(priorities.get('tone')),
        }

        if recommendation_context == 'query':
            entity_analysis = self._query_profile_analyzer.analyze(original_query or search_text)
            query_type = str(entity_analysis.get('type') or 'recommendation')
            title_like = bool(entity_analysis.get('title_like'))
            require_exact = bool(entity_analysis.get('require_exact'))
            keywords = list(entity_analysis.get('keywords') or keywords)[:8]
            if query_type in {'recommendation', 'theme', 'mixed'}:
                priorities = entity_analysis.get('priorities') or priorities
                has_features = entity_analysis.get('has_features') or has_features

        return {
            'type': query_type,
            'search_text': search_text,
            'original_query': original_query,
            'keywords': keywords,
            'features': analysis.get('features') or {},
            'priorities': priorities,
            'has_features': has_features,
            'title_like': title_like,
            'require_exact': require_exact,
        }

    def _merge_qdrant_hits(self, candidate_map: dict[str, dict[str, Any]], hits: list[dict[str, Any]], matched_query: str = '') -> None:
        for hit in hits:
            payload = hit.get('payload') or {}
            book_id = str(payload.get('book_id') or '')
            if not book_id:
                continue
            book = self.get_book(book_id)
            if not book:
                continue
            existing = candidate_map.get(book_id)
            if existing is None:
                candidate_map[book_id] = {
                    'book': book,
                    'search_score': float(hit.get('score', 0.0)),
                    'snippet': '',
                    'matched_queries': [matched_query] if matched_query else [],
                }
                continue
            existing['search_score'] = max(float(existing.get('search_score', 0.0)), float(hit.get('score', 0.0)))
            if matched_query:
                existing.setdefault('matched_queries', []).append(matched_query)

    def _qdrant_query_hits(self, search_profile: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        if not self.settings.online_search_qdrant_enabled or not self.qdrant.enabled:
            return []
        query_type = str(search_profile.get('type') or '')
        if query_type in {'title', 'entity'}:
            return []
        query_text = str(search_profile.get('search_text') or search_profile.get('original_query') or '').strip()
        query_vector = get_query_embedding(query_text)
        if query_vector is None:
            return []
        requested_limit = max(limit * max(1, self.settings.online_search_qdrant_limit_factor), 4)
        return self.qdrant.search_books(query_vector.tolist(), limit=requested_limit)

    def _candidate_results(self, profile: dict[str, Any], mode: str, limit: int, session_id: str | None = None, seed_book_id: str | None = None, query: str | None = None, exclude_book_ids: list[str] | None = None, query_understanding: dict[str, Any] | None = None, recommendation_context: str = 'profile'):
        seed_book = self.get_book(seed_book_id) if seed_book_id else None
        base_query = self._build_live_query(profile, mode, query, seed_book, session_id, query_understanding)
        profile_query = self._query_from_profile(profile, mode, seed_book, session_id) if recommendation_context == 'profile' else ''
        if self.ingestion.background_pressure():
            return self._lightweight_candidate_results(profile, mode, limit, session_id, seed_book, base_query, profile_query, query, exclude_book_ids, query_understanding, recommendation_context)
        candidate_map: dict[str, dict[str, Any]] = {}
        base_top_k = max(limit * self.settings.online_search_candidate_factor, self.settings.online_search_min_candidates)

        focused_search_profile = None
        if recommendation_context == 'query' and query and query.strip():
            focused_search_profile = self._query_profile_analyzer.analyze(query)
            focused_type = str(focused_search_profile.get('type') or 'recommendation')
            if focused_type in {'title', 'entity', 'specific', 'scene'}:
                search_payload = self._agent.process_query(str(query).strip())
                indexed_books = load_index(force=True)
                for item in list(search_payload.get('results') or [])[:base_top_k]:
                    raw_title = str(item.get('title') or '').strip()
                    if not raw_title:
                        continue
                    raw_key = normalize_label(raw_title)
                    book = next(
                        (
                            entry
                            for entry in indexed_books
                            if raw_key
                            and raw_key in {
                                normalize_label(str(entry.get('title') or '')),
                                normalize_label(Path(str(entry.get('source_filename') or '')).stem),
                            }
                        ),
                        None,
                    )
                    if not book:
                        continue
                    book_id = str(book.get('id') or '')
                    if not book_id:
                        continue
                    candidate_map[book_id] = {
                        'book': book,
                        'search_score': float(item.get('relevance', 0.0)),
                        'snippet': str(item.get('snippet') or ''),
                        'matched_queries': [str(query).strip()],
                    }

        search_specs: list[tuple[str, int]] = []
        if base_query and not candidate_map:
            search_specs.append((base_query, base_top_k))
        if profile_query and normalize_label(profile_query) != normalize_label(base_query):
            search_specs.append((profile_query, max(limit * 2, self.settings.online_search_min_candidates)))
        if seed_book and seed_book.get('title'):
            seed_query = f"{seed_book.get('title')} {' '.join(get_genres(seed_book)[:2])}".strip()
            if normalize_label(seed_query) not in {normalize_label(item[0]) for item in search_specs}:
                search_specs.append((seed_query, max(limit + 4, self.settings.online_search_min_candidates // 2)))

        for search_text, top_k in search_specs[: self.settings.online_search_max_specs]:
            search_profile = self._search_profile(base_query or search_text, search_text, recommendation_context)
            self._merge_qdrant_hits(candidate_map, self._qdrant_query_hits(search_profile, top_k), search_text)
            for item in search_hybrid(search_text, top_k=top_k, use_cache=True, query_profile=search_profile):
                book_id = str(item.get('book_id'))
                book = self.get_book(book_id)
                if not book:
                    continue
                existing = candidate_map.get(book_id)
                if existing is None:
                    candidate_map[book_id] = {'book': book, 'search_score': float(item.get('similarity', 0.0)), 'snippet': item.get('snippet', ''), 'matched_queries': [search_text]}
                    continue
                existing['search_score'] = max(float(existing.get('search_score', 0.0)), float(item.get('similarity', 0.0)))
                if not existing.get('snippet'):
                    existing['snippet'] = item.get('snippet', '')
                existing.setdefault('matched_queries', []).append(search_text)

        if seed_book and str(seed_book.get('id')) not in candidate_map:
            candidate_map[str(seed_book.get('id'))] = {'book': seed_book, 'search_score': 0.85, 'snippet': self._preview_excerpt(seed_book, base_query), 'matched_queries': [seed_book.get('title', '')]}

        for match in find_title_matches(query or '', limit=2):
            book_id = str(match.get('book_id') or '')
            if book_id and book_id not in candidate_map:
                book = self.get_book(book_id)
                if book:
                    candidate_map[book_id] = {'book': book, 'search_score': float(match.get('score', 0.0)), 'snippet': self._preview_excerpt(book, query), 'matched_queries': [query or '']}

        if len(candidate_map) <= limit:
            for book in self._cold_start_books(max(limit * 2, 12)):
                candidate_map.setdefault(str(book.get('id')), {'book': book, 'search_score': 0.0, 'snippet': '', 'matched_queries': []})

        seen_books = {str(item) for item in (profile.get('seen_book_ids') or [])}
        excluded = {str(item) for item in (exclude_book_ids or []) if str(item).strip()}
        filtered = []
        for book_id, payload in candidate_map.items():
            if book_id in excluded:
                continue
            if recommendation_context == 'profile' and mode == 'similar' and book_id in seen_books and book_id not in set(profile.get('saved_books') or []):
                continue
            payload['matched_queries'] = list(dict.fromkeys(str(item) for item in payload.get('matched_queries') or [] if str(item).strip()))
            filtered.append(payload)

        if len(filtered) <= limit:
            present_ids = {str(item.get('book', {}).get('id')) for item in filtered if item.get('book')}
            for book in self._cold_start_books(max(limit * 3, 18)):
                book_id = str(book.get('id'))
                if not book_id or book_id in present_ids or book_id in excluded:
                    continue
                if recommendation_context == 'profile' and mode == 'similar' and book_id in seen_books and book_id not in set(profile.get('saved_books') or []):
                    continue
                filtered.append({'book': book, 'search_score': 0.0, 'snippet': '', 'matched_queries': []})
                present_ids.add(book_id)
                if len(filtered) >= max(limit * 2, 6):
                    break
        return filtered, base_query

    def _feature_query_score(self, book: dict[str, Any], search_profile: dict[str, Any]) -> float:
        priorities = (search_profile or {}).get('priorities') or {}
        if not priorities:
            return 0.0
        features = book_feature_map(book)
        score = 0.0
        for category, values in priorities.items():
            category_scores = features.get(category, {})
            for feature_name, weight in values[:2]:
                score += float(category_scores.get(feature_name, 0.0)) * float(weight)
        return round(score, 6)

    def _lightweight_candidate_results(self, profile: dict[str, Any], mode: str, limit: int, session_id: str | None, seed_book: dict[str, Any] | None, base_query: str, profile_query: str, query: str | None, exclude_book_ids: list[str] | None, query_understanding: dict[str, Any] | None, recommendation_context: str):
        search_text = query or base_query or profile_query
        search_profile = self._search_profile(base_query or search_text, search_text or base_query, recommendation_context)
        title_scores = {str(item.get('book_id')): float(item.get('score', 0.0)) for item in find_title_matches(search_text or query or '', limit=max(limit * 4, 12))}
        seen_books = {str(item) for item in (profile.get('seen_book_ids') or [])}
        excluded = {str(item) for item in (exclude_book_ids or []) if str(item).strip()}
        ranked: list[dict[str, Any]] = []

        for book in load_index(force=True):
            book_id = str(book.get('id'))
            if not book_id or book_id in excluded:
                continue
            if recommendation_context == 'profile' and mode == 'similar' and book_id in seen_books and book_id not in set(profile.get('saved_books') or []):
                continue

            if recommendation_context == 'query':
                score = query_match_score(book, query_understanding, search_text)
                score += self._feature_query_score(book, search_profile)
                score += title_scores.get(book_id, 0.0) * 1.15
                if score <= 0.0 and book_id not in title_scores:
                    continue
            else:
                score = float(score_book_for_profile(book, profile, mode, session_id).get('score', 0.0))
                score += self._feature_query_score(book, search_profile) * 0.2
                score += title_scores.get(book_id, 0.0) * 0.1

            ranked.append({'book': book, 'search_score': round(float(score), 6)})

        ranked.sort(key=lambda item: item['search_score'], reverse=True)
        shortlisted = ranked[: max(limit * 4, 12)]
        candidates = [
            {
                'book': item['book'],
                'search_score': item['search_score'],
                'snippet': self._preview_excerpt(item['book'], search_text),
                'matched_queries': [search_text] if search_text else [],
            }
            for item in shortlisted
        ]

        if seed_book and str(seed_book.get('id')) not in {str(item['book'].get('id')) for item in candidates}:
            candidates.insert(0, {'book': seed_book, 'search_score': 0.85, 'snippet': self._preview_excerpt(seed_book, search_text), 'matched_queries': [seed_book.get('title', '')]})

        if len(candidates) <= limit:
            present_ids = {str(item['book'].get('id')) for item in candidates}
            for book in self._cold_start_books(max(limit * 3, 18)):
                book_id = str(book.get('id'))
                if not book_id or book_id in present_ids or book_id in excluded:
                    continue
                candidates.append({'book': book, 'search_score': 0.0, 'snippet': self._preview_excerpt(book, search_text), 'matched_queries': []})
                present_ids.add(book_id)
                if len(candidates) >= max(limit * 2, 6):
                    break

        return candidates, base_query

    @staticmethod
    def _normalize_scores(values: list[float]) -> list[float]:
        if not values:
            return []
        minimum = min(values)
        maximum = max(values)
        if maximum - minimum <= 1e-9:
            base = 1.0 if maximum > 0 else 0.0
            return [base for _ in values]
        return [(value - minimum) / (maximum - minimum) for value in values]

    @staticmethod
    def _profile_score_norm(raw_score: float) -> float:
        return max(0.0, min((float(raw_score) + 1.0) / 2.0, 1.0))

    def _author_boost(self, book: dict[str, Any], profile: dict[str, Any], query_understanding: dict[str, Any] | None) -> float:
        author = get_author(book)
        if not author:
            return 0.0
        author_key = normalize_label(author)
        preferred = {normalize_label(item) for item in (profile.get('preferred_authors') or [])}
        comparables = {normalize_label(item) for item in ((query_understanding or {}).get('comparables') or [])}
        positive_authors = profile.get('positive_authors') or {}
        negative_authors = profile.get('negative_authors') or {}
        boost = 0.0
        if author_key in preferred or author_key in comparables:
            boost += 0.9
        boost += min(0.6, float(positive_authors.get(author_key, 0.0)) * 0.25)
        boost -= min(0.7, float(negative_authors.get(author_key, 0.0)) * 0.3)
        return round(max(-1.0, min(boost, 1.0)), 6)

    def _genre_boost(self, book: dict[str, Any], profile: dict[str, Any], query_understanding: dict[str, Any] | None) -> float:
        genres = get_genres(book)
        if not genres:
            return 0.0
        book_genres = {normalize_label(item) for item in genres}
        preferred = {normalize_label(item) for item in (profile.get('preferred_genres') or [])}
        query_genres = {normalize_label(item) for item in ((query_understanding or {}).get('genres') or [])}
        positive_genres = profile.get('positive_genres') or {}
        negative_genres = profile.get('negative_genres') or {}
        boost = 0.0
        if preferred & book_genres:
            boost += 0.55
        if query_genres & book_genres:
            boost += 0.65
        for genre in book_genres:
            boost += min(0.3, float(positive_genres.get(genre, 0.0)) * 0.12)
            boost -= min(0.35, float(negative_genres.get(genre, 0.0)) * 0.15)
        return round(max(-1.0, min(boost, 1.0)), 6)

    def _exploration_bonus(self, profile_norm: float, mode: str, seen_penalty: float) -> float:
        unseen_bonus = 0.0 if seen_penalty > 0 else 1.0
        if mode == 'new':
            return round(0.6 * unseen_bonus + 0.35 * max(0.0, 1.0 - profile_norm), 6)
        if mode == 'risk':
            return round(0.75 * max(0.0, 1.0 - abs(profile_norm - 0.45)) + 0.2 * unseen_bonus, 6)
        return round(0.15 * unseen_bonus + 0.05 * max(0.0, 1.0 - profile_norm), 6)

    @staticmethod
    def _feedback_alignment(base_breakdown: dict[str, Any]) -> float:
        recent = max(0.0, float(base_breakdown.get('recent_similarity', 0.0)))
        session = max(0.0, float(base_breakdown.get('session_similarity', 0.0)))
        return round(min(1.0, 0.58 * recent + 0.42 * session), 6)

    def _reason_candidates(self, book: dict[str, Any], profile: dict[str, Any], query_understanding: dict[str, Any] | None, matched_queries: list[str], recommendation_context: str) -> list[str]:
        reasons = []
        reasons.extend(query_reason_candidates(book, query_understanding))
        if recommendation_context == 'profile':
            author = get_author(book)
            if author and normalize_label(author) in {normalize_label(item) for item in (profile.get('preferred_authors') or [])}:
                reasons.append(author)
            for genre in get_genres(book):
                if normalize_label(genre) in {normalize_label(item) for item in (profile.get('preferred_genres') or [])}:
                    reasons.append(genre)
        seen = set()
        result = []
        for item in reasons:
            key = normalize_label(item)
            if item and key not in seen:
                seen.add(key)
                result.append(item)
        return result[:5]

    def _serialize_card(self, book: dict[str, Any], mode: str, recommendation_context: str, search_context: str, request_mode: str, candidate: dict[str, Any], score: float, search_score: float, breakdown: dict[str, Any], explanation_payload: dict[str, Any], query: str | None) -> dict[str, Any]:
        book_id = str(book.get('id'))
        hybrid_tags = get_hybrid_tags(book)
        return {
            'book_id': book_id,
            'title': book.get('title'),
            'author': get_author(book),
            'cover_url': get_cover_url(book),
            'genres': [],
            'moods': [],
            'short_summary': self._summary_for_book(book),
            'preview_excerpt': candidate.get('snippet') or self._preview_excerpt(book, query),
            'why_for_you': explanation_payload.get('why_for_you', ''),
            'match_reasons': [],
            'caveats': explanation_payload.get('caveats', []),
            'style_tags': hybrid_tags,
            'read_url': f'/api/v1/books/{book_id}/content',
            'preview_url': f'/api/v1/books/{book_id}/preview',
            'actions': ['like', 'dislike', 'save', 'read', 'swipe_left', 'swipe_right'],
            'format': book.get('format'),
            'snippet': candidate.get('snippet', ''),
            'feature_matches': feature_priorities_from_flattened(book_feature_map(book), 0.12, 2),
            'explanation': explanation_payload.get('why_for_you', ''),
            'recommendation_context': recommendation_context,
            'search_context': search_context,
            'request_mode': request_mode,
            'explanation_mode': explanation_payload.get('explanation_mode', f'{recommendation_context}_explanation'),
            'match_reason_context': recommendation_context,
            'score': round(float(score), 6),
            'search_score': round(float(search_score), 6),
            'score_breakdown': breakdown,
            'mode': mode,
        }

    def _sync_book_to_qdrant(self, book: dict):
        if self.qdrant.enabled and book:
            passport = book.get('semantic_passport') or {}
            semantic_vector = get_book_embedding(str(book.get('id')))
            if semantic_vector is None:
                return
            self.qdrant.upsert_book(
                str(book.get('id')),
                semantic_vector.tolist(),
                {
                    'book_id': book.get('id'),
                    'title': book.get('title'),
                    'format': book.get('format'),
                    'author': get_author(book),
                    'themes': passport.get('themes') or [],
                    'entities': passport.get('entities') or [],
                    'summary': passport.get('summary') or get_summary(book),
                },
            )

    def _sync_user_profile_to_qdrant(self, profile: dict[str, Any]):
        if self.qdrant.enabled and profile:
            self.qdrant.upsert_user_vector(str(profile.get('user_id')), feature_vector(build_long_term_taste(profile)), {'user_id': profile.get('user_id'), 'last_updated': profile.get('last_updated')})

    def sync_catalog_to_qdrant(self):
        if not self.qdrant.enabled:
            return {'status': 'disabled', 'synced': 0}
        synced = 0
        failed = 0
        for book in load_index(force=True):
            if self._sync_book_to_qdrant(book):
                synced += 1
            else:
                failed += 1
        return {'status': 'ok' if failed == 0 else 'partial', 'synced': synced, 'failed': failed}

    def get_recommendations(self, user_id: str, mode: str = 'similar', limit: int = 10, session_id: str | None = None, seed_book_id: str | None = None, query: str | None = None, exclude_book_ids: list[str] | None = None, cursor: str | None = None, offset: int | None = None, recommendation_context: str | None = None, search_context: str | None = None):
        started = time.perf_counter()
        exclude_book_ids = [str(item) for item in (exclude_book_ids or []) if str(item).strip()]
        search_context, recommendation_context, request_mode = self._resolve_contexts(recommendation_context, search_context, query)
        cursor_key = str(cursor or '').strip() or 'none'
        offset_key = 'none' if offset is None else str(int(offset))
        cache_key = f"feed:{user_id}:{search_context}:{recommendation_context}:{request_mode}:{mode}:{session_id or 'global'}:{seed_book_id or 'none'}:{limit}:{normalize_label(query or '')}:{','.join(exclude_book_ids)}:{cursor_key}:{offset_key}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        with self._foreground_request():
            profile = self._ensure_profile(user_id)
            profile_summary = taste_profile_summary(profile, session_id)
            active_profile = profile if recommendation_context == 'profile' else empty_profile(user_id)
            query_understanding = understand_query(query) if recommendation_context == 'query' and query and query.strip() else None
            candidates, resolved_query = self._candidate_results(profile, mode, limit, session_id, seed_book_id, query, exclude_book_ids, query_understanding, recommendation_context)
            if not candidates:
                payload = {'user_id': user_id, 'mode': mode, 'search_context': search_context, 'recommendation_context': recommendation_context, 'request_mode': request_mode, 'query': query or resolved_query, 'query_understanding': query_understanding or {}, 'items': [], 'count': 0, 'profile': profile_summary, 'seed_book_id': seed_book_id, 'cursor': cursor, 'offset': offset or 0}
                self.cache.set(cache_key, payload, self.settings.cache_ttl_sec)
                return payload

            search_norms = self._normalize_scores([float(item.get('search_score', 0.0)) for item in candidates])
            ranked = []
            for idx, candidate in enumerate(candidates):
                book = candidate['book']
                base_breakdown = score_book_for_profile(book, active_profile, mode, session_id)
                profile_norm = self._profile_score_norm(base_breakdown['score']) if recommendation_context == 'profile' else 0.0
                query_score = query_match_score(book, query_understanding, query or resolved_query)
                query_reward = max(0.0, query_score)
                query_penalty = max(0.0, -query_score)
                author_boost = self._author_boost(book, profile if recommendation_context == 'profile' else {}, query_understanding)
                genre_boost = self._genre_boost(book, profile if recommendation_context == 'profile' else {}, query_understanding)
                feedback_alignment = self._feedback_alignment(base_breakdown) if recommendation_context == 'profile' else 0.0
                seen_penalty = 0.24 if recommendation_context == 'profile' and str(book.get('id')) in set(profile.get('seen_book_ids') or []) else 0.0
                exploration_bonus = self._exploration_bonus(profile_norm, mode, seen_penalty)
                is_query_context = recommendation_context == 'query'
                search_weight = 0.55 if is_query_context else 0.35
                profile_weight = 0.0 if is_query_context else 0.25
                query_weight = 0.32 if is_query_context else 0.08
                feedback_weight = 0.0 if is_query_context else 0.1
                query_penalty_weight = 0.38 if is_query_context else 0.06
                exploration_weight = 0.0 if is_query_context else 0.06
                author_weight = 0.04 if is_query_context else 0.08
                genre_weight = 0.09 if is_query_context else 0.08
                total_score = (
                    search_weight * search_norms[idx]
                    + profile_weight * profile_norm
                    + query_weight * query_reward
                    + author_weight * max(0.0, author_boost)
                    + genre_weight * max(0.0, genre_boost)
                    + feedback_weight * feedback_alignment
                    + exploration_weight * exploration_bonus
                    - query_penalty_weight * query_penalty
                    - seen_penalty
                )
                breakdown = dict(base_breakdown)
                breakdown.update({
                    'normalized_search': round(search_norms[idx], 6),
                    'normalized_profile': round(profile_norm, 6),
                    'query_match': round(query_score, 6),
                    'query_reward': round(query_reward, 6),
                    'query_penalty': round(query_penalty, 6),
                    'author_boost': round(author_boost, 6),
                    'genre_boost': round(genre_boost, 6),
                    'feedback_alignment': round(feedback_alignment, 6),
                    'exploration_bonus': round(exploration_bonus, 6),
                    'seen_penalty': round(seen_penalty, 6),
                    'dislike_penalty': 0.0,
                })
                ranked.append({'book': book, 'candidate': candidate, 'score': round(total_score, 6), 'search_score': round(float(candidate.get('search_score', 0.0)), 6), 'breakdown': breakdown})

            ranked.sort(key=lambda item: item['score'], reverse=True)

            start_offset = int(offset or 0)
            if cursor and cursor.isdigit():
                start_offset = int(cursor)
            sliced = ranked[start_offset:start_offset + limit]
            items = []
            for idx, item in enumerate(sliced):
                reason_candidates = self._reason_candidates(item['book'], profile, query_understanding, item['candidate'].get('matched_queries') or [], recommendation_context)
                explanation_payload = generate_card_explanation(
                    item['book'],
                    query or resolved_query,
                    query_understanding,
                    item['breakdown'],
                    reason_candidates,
                    profile_summary if recommendation_context == 'profile' else None,
                    recommendation_context=recommendation_context,
                    use_llm=idx == 0 and recommendation_context == 'profile',
                )
                items.append(self._serialize_card(item['book'], mode, recommendation_context, search_context, request_mode, item['candidate'], item['score'], item['search_score'], item['breakdown'], explanation_payload, query or resolved_query))

            next_offset = start_offset + len(items)
            next_cursor = str(next_offset) if next_offset < len(ranked) else None
            payload = {
                'user_id': user_id,
                'mode': mode,
                'search_context': search_context,
                'recommendation_context': recommendation_context,
                'request_mode': request_mode,
                'query': query or resolved_query,
                'resolved_query': resolved_query,
                'query_understanding': query_understanding or {},
                'items': items,
                'count': len(items),
                'profile': profile_summary,
                'seed_book_id': seed_book_id,
                'cursor': next_cursor,
                'offset': start_offset,
            }
            LOGGER.info(
                'recommendation_feed_completed user_id=%s recommendation_context=%s mode=%s count=%s duration_ms=%s',
                user_id,
                recommendation_context,
                mode,
                len(items),
                int((time.perf_counter() - started) * 1000),
            )
            self.cache.set(cache_key, payload, self.settings.cache_ttl_sec)
            return payload
