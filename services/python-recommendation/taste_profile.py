from __future__ import annotations

import time
from collections import Counter
from typing import Any

from book_metadata import get_author, get_genres, normalize_label
from catalog_utils import book_feature_map, cosine_similarity_from_maps, dominant_feature_names, empty_feature_map, feature_priorities_from_flattened, inflate_feature_scores
from search_engine import TEXT_FEATURES, find_title_matches, load_index

POSITIVE_ACTIONS = {'like', 'save', 'swipe_right'}
NEGATIVE_ACTIONS = {'dislike', 'swipe_left'}
NEUTRAL_ACTIONS = {'skip'}
ALL_ACTIONS = POSITIVE_ACTIONS | NEGATIVE_ACTIONS | NEUTRAL_ACTIONS
ACTION_STRENGTH = {'like': 1.0, 'save': 1.25, 'swipe_right': 0.9, 'dislike': 1.0, 'swipe_left': 0.8, 'skip': 0.12}
FAVORITE_ACTIONS = {'like', 'save'}
PROFILE_POSITIVE_ACTIONS = {'save', 'swipe_right'}
PROFILE_NEGATIVE_ACTIONS = {'swipe_left'}


def empty_profile(user_id: str) -> dict[str, Any]:
    now = time.time()
    return {
        'user_id': user_id,
        'positive_features': empty_feature_map(),
        'negative_features': empty_feature_map(),
        'recent_positive_features': empty_feature_map(),
        'recent_negative_features': empty_feature_map(),
        'session_vectors': {},
        'interaction_counters': {action: 0 for action in sorted(ALL_ACTIONS)},
        'positive_books': [],
        'negative_books': [],
        'saved_books': [],
        'seen_book_ids': [],
        'favorite_book_ids': [],
        'preferred_authors': [],
        'preferred_genres': [],
        'positive_authors': {},
        'negative_authors': {},
        'positive_genres': {},
        'negative_genres': {},
        'onboarding': {},
        'created_at': now,
        'last_updated': now,
    }


def _as_string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = str(item).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _as_feature_map(value: Any) -> dict[str, float]:
    template = empty_feature_map()
    if not isinstance(value, dict):
        return template
    for key in template:
        try:
            template[key] = round(float(value.get(key, 0.0)), 6)
        except (TypeError, ValueError):
            template[key] = 0.0
    return template


def _as_score_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        cleaned = normalize_label(key)
        if not cleaned:
            continue
        try:
            result[cleaned] = round(float(raw), 6)
        except (TypeError, ValueError):
            continue
    return result


def _as_session_vectors(value: Any) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, float]] = {}
    for session_id, feature_map in value.items():
        cleaned = str(session_id).strip()
        if not cleaned:
            continue
        result[cleaned] = _as_feature_map(feature_map)
    return result


def normalize_profile(profile: Any, user_id: str | None = None) -> dict[str, Any]:
    payload = profile if isinstance(profile, dict) else {}
    resolved_user_id = str(user_id or payload.get('user_id') or '').strip()
    normalized = empty_profile(resolved_user_id)
    normalized['positive_features'] = _as_feature_map(payload.get('positive_features'))
    normalized['negative_features'] = _as_feature_map(payload.get('negative_features'))
    normalized['recent_positive_features'] = _as_feature_map(payload.get('recent_positive_features'))
    normalized['recent_negative_features'] = _as_feature_map(payload.get('recent_negative_features'))
    normalized['session_vectors'] = _as_session_vectors(payload.get('session_vectors'))
    normalized['interaction_counters'] = {
        action: int((payload.get('interaction_counters') or {}).get(action, 0))
        for action in sorted(ALL_ACTIONS)
    }
    normalized['positive_books'] = _as_string_list(payload.get('positive_books'), 24)
    normalized['negative_books'] = _as_string_list(payload.get('negative_books'), 24)
    normalized['saved_books'] = _as_string_list(payload.get('saved_books'), 48)
    normalized['seen_book_ids'] = _as_string_list(payload.get('seen_book_ids'), 128)
    normalized['favorite_book_ids'] = _as_string_list(payload.get('favorite_book_ids'), 12)
    normalized['preferred_authors'] = _as_string_list(payload.get('preferred_authors'), 10)
    normalized['preferred_genres'] = _as_string_list(payload.get('preferred_genres'), 10)
    normalized['positive_authors'] = _as_score_map(payload.get('positive_authors'))
    normalized['negative_authors'] = _as_score_map(payload.get('negative_authors'))
    normalized['positive_genres'] = _as_score_map(payload.get('positive_genres'))
    normalized['negative_genres'] = _as_score_map(payload.get('negative_genres'))
    normalized['onboarding'] = dict(payload.get('onboarding') or {})
    normalized['created_at'] = float(payload.get('created_at') or normalized['created_at'])
    normalized['last_updated'] = float(payload.get('last_updated') or normalized['last_updated'])
    return normalized


def profile_needs_bootstrap(profile: Any) -> bool:
    if not isinstance(profile, dict):
        return False
    return any(
        key not in profile
        for key in (
            'positive_features',
            'negative_features',
            'recent_positive_features',
            'recent_negative_features',
            'session_vectors',
            'positive_authors',
            'negative_authors',
            'positive_genres',
            'negative_genres',
        )
    )


def _limited_unique(values: list[str], value: str, limit: int):
    if not value:
        return
    if value in values:
        values.remove(value)
    values.insert(0, value)
    del values[limit:]


def _blend(target: dict[str, float], source: dict[str, float], strength: float, decay: float):
    for key, value in source.items():
        target[key] = round(float(target.get(key, 0.0)) * decay + float(value) * strength, 6)


def _lookup_feature_key(raw: str) -> str | None:
    probe = str(raw or '').strip().lower()
    if not probe:
        return None
    for category, subcats in TEXT_FEATURES.items():
        for subcat in subcats:
            normalized = subcat.lower()
            if probe == normalized or probe == f'{category}.{normalized}' or probe in normalized or normalized in probe:
                return f'{category}.{subcat}'
    return None


def _bump_affinity(target: dict[str, float], raw: str, amount: float, decay: float = 0.92):
    key = normalize_label(raw)
    if not key:
        return
    current = float(target.get(key, 0.0))
    target[key] = round(current * decay + amount, 6)


def _book_lookup_by_id() -> dict[str, dict[str, Any]]:
    return {str(book.get('id')): book for book in load_index(force=True)}


def apply_onboarding(profile: dict[str, Any], payload: dict[str, Any] | None):
    profile['onboarding'] = dict(payload or {})
    positive = empty_feature_map()
    negative = empty_feature_map()
    onboarding = profile['onboarding']

    for key in ('favorite_moods', 'favorite_atmosphere', 'favorite_tone', 'favorite_style', 'favorite_plot'):
        for raw in onboarding.get(key) or []:
            mapped = _lookup_feature_key(raw)
            if mapped:
                positive[mapped] += 0.6

    for raw in onboarding.get('avoid_tags') or []:
        mapped = _lookup_feature_key(raw)
        if mapped:
            negative[mapped] += 0.6

    preferred_authors = []
    for raw in onboarding.get('favorite_authors') or []:
        cleaned = str(raw).strip()
        if cleaned:
            preferred_authors.append(cleaned)
            _bump_affinity(profile.setdefault('positive_authors', {}), cleaned, 0.85, decay=0.96)
    profile['preferred_authors'] = preferred_authors[:10]

    preferred_genres = []
    for raw in onboarding.get('favorite_genres') or []:
        cleaned = str(raw).strip()
        if cleaned:
            preferred_genres.append(cleaned)
            _bump_affinity(profile.setdefault('positive_genres', {}), cleaned, 0.8, decay=0.96)
    profile['preferred_genres'] = preferred_genres[:10]

    favorite_book_ids: list[str] = []
    book_lookup = _book_lookup_by_id()
    books_text = ' '.join(str(item) for item in onboarding.get('favorite_books') or [])
    for raw in onboarding.get('favorite_books') or []:
        match = next(iter(find_title_matches(str(raw), limit=1)), None)
        if not match:
            continue
        book_id = str(match.get('book_id') or '')
        if not book_id:
            continue
        favorite_book_ids.append(book_id)
        book = book_lookup.get(book_id)
        if book:
            _blend(positive, book_feature_map(book), 0.42, 1.0)
            author = get_author(book)
            if author:
                _bump_affinity(profile.setdefault('positive_authors', {}), author, 0.62, decay=1.0)
            for genre in get_genres(book):
                _bump_affinity(profile.setdefault('positive_genres', {}), genre, 0.42, decay=1.0)
    profile['favorite_book_ids'] = favorite_book_ids[:12]

    if 'тайн' in books_text.lower():
        positive['plot.тайна'] += 0.35

    _blend(profile['positive_features'], positive, 0.5, 0.92)
    _blend(profile['negative_features'], negative, 0.45, 0.92)
    profile['last_updated'] = time.time()
    return profile


def _session_vector(profile: dict[str, Any], session_id: str) -> dict[str, float]:
    current = (profile.get('session_vectors') or {}).get(session_id)
    if isinstance(current, dict):
        return current
    current = empty_feature_map()
    profile.setdefault('session_vectors', {})[session_id] = current
    return current


def update_profile_from_interaction(profile: dict[str, Any], book: dict, action: str, session_id: str | None = None, metadata: dict[str, Any] | None = None):
    action = str(action or '').strip().lower()
    if action not in ALL_ACTIONS:
        raise ValueError(f'Unsupported interaction action: {action}')
    profile.setdefault('interaction_counters', {name: 0 for name in sorted(ALL_ACTIONS)})
    profile['interaction_counters'][action] = int(profile['interaction_counters'].get(action, 0)) + 1
    profile['last_updated'] = time.time()
    book_id = str((book or {}).get('id') or '')
    if book_id:
        _limited_unique(profile.setdefault('seen_book_ids', []), book_id, 128)
    feature_map = book_feature_map(book)
    strength = ACTION_STRENGTH[action]

    if action in FAVORITE_ACTIONS and book_id:
        _limited_unique(profile.setdefault('saved_books', []), book_id, 48)
        _limited_unique(profile.setdefault('favorite_book_ids', []), book_id, 48)

    if action in PROFILE_POSITIVE_ACTIONS:
        _blend(profile['positive_features'], feature_map, 0.32 * strength, 0.985)
        _blend(profile['recent_positive_features'], feature_map, 0.55 * strength, 0.72)
        if session_id:
            _blend(_session_vector(profile, session_id), feature_map, 0.48 * strength, 0.68)
        if book_id:
            _limited_unique(profile.setdefault('positive_books', []), book_id, 24)
        author = get_author(book)
        if author:
            _bump_affinity(profile.setdefault('positive_authors', {}), author, 0.55 * strength)
        for genre in get_genres(book):
            _bump_affinity(profile.setdefault('positive_genres', {}), genre, 0.4 * strength)
    elif action in PROFILE_NEGATIVE_ACTIONS:
        _blend(profile['negative_features'], feature_map, 0.34 * strength, 0.985)
        _blend(profile['recent_negative_features'], feature_map, 0.58 * strength, 0.72)
        if session_id:
            _blend(_session_vector(profile, session_id), feature_map, -0.28 * strength, 0.68)
        if book_id:
            _limited_unique(profile.setdefault('negative_books', []), book_id, 24)
        author = get_author(book)
        if author:
            _bump_affinity(profile.setdefault('negative_authors', {}), author, 0.55 * strength)
        for genre in get_genres(book):
            _bump_affinity(profile.setdefault('negative_genres', {}), genre, 0.4 * strength)
    elif action in NEUTRAL_ACTIONS:
        _blend(profile['recent_negative_features'], feature_map, 0.18 * max(strength, 0.1), 0.75)
        if session_id:
            _blend(_session_vector(profile, session_id), feature_map, -0.06 * max(strength, 0.1), 0.72)
    return profile


def build_long_term_taste(profile: dict[str, Any]) -> dict[str, float]:
    result = empty_feature_map()
    for key in result:
        result[key] = round(float(profile.get('positive_features', {}).get(key, 0.0)) - 0.8 * float(profile.get('negative_features', {}).get(key, 0.0)), 6)
    return result


def build_session_taste(profile: dict[str, Any], session_id: str | None = None) -> dict[str, float]:
    result = empty_feature_map()
    if session_id:
        session_vector = (profile.get('session_vectors') or {}).get(session_id) or {}
        for key in result:
            result[key] = round(float(session_vector.get(key, 0.0)), 6)
    else:
        for key in result:
            result[key] = round(float(profile.get('recent_positive_features', {}).get(key, 0.0)) - 0.7 * float(profile.get('recent_negative_features', {}).get(key, 0.0)), 6)
    return result


def combined_taste(profile: dict[str, Any], session_id: str | None = None) -> dict[str, float]:
    long_term = build_long_term_taste(profile)
    session_term = build_session_taste(profile, session_id)
    result = empty_feature_map()
    for key in result:
        result[key] = round(0.72 * float(long_term.get(key, 0.0)) + 0.28 * float(session_term.get(key, 0.0)), 6)
    return result


def recommendation_priorities(profile: dict[str, Any], session_id: str | None = None):
    return feature_priorities_from_flattened(combined_taste(profile, session_id), 0.08, 2)


def score_book_for_profile(book: dict, profile: dict[str, Any], mode: str = 'similar', session_id: str | None = None) -> dict[str, float]:
    book_map = book_feature_map(book)
    pos = cosine_similarity_from_maps(book_map, profile.get('positive_features') or {})
    neg = cosine_similarity_from_maps(book_map, profile.get('negative_features') or {})
    recent = cosine_similarity_from_maps(book_map, profile.get('recent_positive_features') or {}) - 0.45 * cosine_similarity_from_maps(book_map, profile.get('recent_negative_features') or {})
    session = cosine_similarity_from_maps(book_map, build_session_taste(profile, session_id))

    author_key = normalize_label(get_author(book))
    preferred_authors = {normalize_label(item) for item in (profile.get('preferred_authors') or [])}
    author_affinity = 0.0
    if author_key:
        if author_key in preferred_authors:
            author_affinity += 0.8
        author_affinity += min(0.6, float((profile.get('positive_authors') or {}).get(author_key, 0.0)) * 0.25)
        author_affinity -= min(0.8, float((profile.get('negative_authors') or {}).get(author_key, 0.0)) * 0.3)

    preferred_genres = {normalize_label(item) for item in (profile.get('preferred_genres') or [])}
    positive_genres = profile.get('positive_genres') or {}
    negative_genres = profile.get('negative_genres') or {}
    genre_affinity = 0.0
    for genre in get_genres(book):
        genre_key = normalize_label(genre)
        if genre_key in preferred_genres:
            genre_affinity += 0.2
        genre_affinity += min(0.15, float(positive_genres.get(genre_key, 0.0)) * 0.08)
        genre_affinity -= min(0.18, float(negative_genres.get(genre_key, 0.0)) * 0.1)

    seen_penalty = 0.35 if str(book.get('id')) in set(profile.get('seen_book_ids') or []) else 0.0
    if mode == 'new':
        diversity = max(0.0, 1.0 - pos)
        score = 0.38 * pos + 0.16 * recent + 0.28 * diversity - 0.22 * neg + 0.08 * session + 0.08 * author_affinity + 0.08 * genre_affinity - seen_penalty
    elif mode == 'risk':
        serendipity = max(0.0, 1.0 - abs(pos - 0.55))
        score = 0.26 * pos + 0.14 * recent + 0.36 * serendipity - 0.18 * neg + 0.12 * session + 0.06 * author_affinity + 0.08 * genre_affinity - 0.1 * seen_penalty
    else:
        score = 0.58 * pos + 0.2 * recent + 0.1 * session + 0.08 * author_affinity + 0.1 * genre_affinity - 0.32 * neg - seen_penalty

    return {
        'score': round(float(score), 6),
        'positive_similarity': round(float(pos), 6),
        'negative_similarity': round(float(neg), 6),
        'recent_similarity': round(float(recent), 6),
        'session_similarity': round(float(session), 6),
        'author_affinity': round(float(author_affinity), 6),
        'genre_affinity': round(float(genre_affinity), 6),
    }


def taste_profile_summary(profile: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    positive_tree = inflate_feature_scores(profile.get('positive_features') or {})
    negative_tree = inflate_feature_scores(profile.get('negative_features') or {})
    combined_tree = inflate_feature_scores(combined_taste(profile, session_id))
    return {
        'user_id': profile.get('user_id'),
        'dominant_taste': dominant_feature_names(combined_tree, 0.1, 2),
        'positive_affinities': dominant_feature_names(positive_tree, 0.12, 2),
        'avoidance_signals': dominant_feature_names(negative_tree, 0.12, 2),
        'interaction_counters': dict(Counter(profile.get('interaction_counters') or {})),
        'recent_positive_books': list(profile.get('positive_books') or [])[:5],
        'recent_negative_books': list(profile.get('negative_books') or [])[:5],
        'saved_books': list(profile.get('saved_books') or [])[:8],
        'favorite_book_ids': list(profile.get('favorite_book_ids') or [])[:8],
        'preferred_authors': list(profile.get('preferred_authors') or [])[:8],
        'preferred_genres': list(profile.get('preferred_genres') or [])[:8],
        'priorities': recommendation_priorities(profile, session_id),
        'onboarding': profile.get('onboarding') or {},
        'last_updated': profile.get('last_updated'),
    }
