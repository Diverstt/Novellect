from __future__ import annotations

import re
from typing import Any

from catalog_utils import dominant_feature_names


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = re.split(r'[,;|/]+', str(value))
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item).strip()
        key = normalize_label(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def normalize_label(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'\s+', ' ', text)
    return text


def metadata_dict(book: dict[str, Any] | None) -> dict[str, Any]:
    metadata = (book or {}).get('metadata') or {}
    return metadata if isinstance(metadata, dict) else {}


def semantic_passport(book: dict[str, Any] | None) -> dict[str, Any]:
    passport = (book or {}).get('semantic_passport') or {}
    return passport if isinstance(passport, dict) else {}


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def get_author(book: dict[str, Any] | None) -> str:
    metadata = metadata_dict(book)
    return _first_non_empty(
        (book or {}).get('author'),
        metadata.get('author'),
        metadata.get('authors') if isinstance(metadata.get('authors'), str) else '',
        metadata.get('writer'),
        metadata.get('creator'),
    )


def get_cover_url(book: dict[str, Any] | None) -> str:
    metadata = metadata_dict(book)
    return _first_non_empty(
        (book or {}).get('cover_url'),
        metadata.get('cover_url'),
        metadata.get('image_url'),
        metadata.get('thumbnail_url'),
        metadata.get('cover'),
    )


def get_genres(book: dict[str, Any] | None) -> list[str]:
    metadata = metadata_dict(book)
    genres = []
    for key in ('genres', 'genre', 'categories', 'tags'):
        genres.extend(as_list(metadata.get(key)))
    return genres[:6]


def get_moods(book: dict[str, Any] | None) -> list[str]:
    passport = semantic_passport(book)
    passport_moods = as_list(passport.get('moods')) + as_list(passport.get('tones'))
    if passport_moods:
        return passport_moods[:6]
    metadata = metadata_dict(book)
    moods = as_list(metadata.get('moods')) + as_list(metadata.get('mood'))
    if moods:
        return moods[:6]
    features = (book or {}).get('features') or {}
    picked: list[str] = []
    for category in ('mood', 'atmosphere', 'tone'):
        for name, _score in (dominant_feature_names({category: features.get(category, {})}, 0.12, 2).get(category) or []):
            if normalize_label(name) not in {normalize_label(item) for item in picked}:
                picked.append(name)
    return picked[:6]


def get_theme_hints(book: dict[str, Any] | None) -> list[str]:
    passport = semantic_passport(book)
    passport_hints = as_list(passport.get('themes')) + as_list(passport.get('scene_hints'))
    if passport_hints:
        return passport_hints[:8]
    features = (book or {}).get('features') or {}
    hints: list[str] = []
    seen: set[str] = set()
    for category in ('plot', 'characters', 'style', 'atmosphere', 'tone'):
        for name, _score in (dominant_feature_names({category: features.get(category, {})}, 0.12, 2).get(category) or []):
            key = normalize_label(name)
            if key and key not in seen:
                seen.add(key)
                hints.append(name)
    return hints[:8]


def get_summary(book: dict[str, Any] | None) -> str:
    passport = semantic_passport(book)
    summary = _first_non_empty(passport.get('summary'))
    if summary:
        return summary
    metadata = metadata_dict(book)
    summary = _first_non_empty(
        (book or {}).get('summary'),
        metadata.get('short_summary'),
        metadata.get('summary'),
        metadata.get('description'),
        metadata.get('annotation'),
        metadata.get('blurb'),
    )
    if summary:
        return summary
    hints = get_theme_hints(book)
    title = (book or {}).get('title') or 'Эта книга'
    if hints:
        return f"{title} — история с акцентом на {', '.join(hints[:3])}."
    return f"{title} — книга из каталога Novellect."


def get_style_tags(book: dict[str, Any] | None) -> list[str]:
    passport = semantic_passport(book)
    tags: list[str] = []
    seen: set[str] = set()
    for source in (
        as_list(passport.get('moods')),
        as_list(passport.get('tones')),
        as_list(passport.get('themes')),
        as_list(passport.get('scene_hints')),
        get_genres(book),
        get_moods(book),
        get_theme_hints(book),
    ):
        for item in source:
            key = normalize_label(item)
            if key and key not in seen:
                seen.add(key)
                tags.append(item)
    return tags[:6]


def get_hybrid_tags(book: dict[str, Any] | None, limit: int = 6) -> list[str]:
    passport = semantic_passport(book)
    passport_tags = []
    for source in (
        as_list(passport.get('moods')),
        as_list(passport.get('tones')),
        as_list(passport.get('themes')),
        as_list(passport.get('scene_hints')),
    ):
        passport_tags.extend(source)
    passport_tags = as_list(passport_tags)
    if passport_tags:
        return passport_tags[:limit]
    features = (book or {}).get('features') or {}
    category_order = ('mood', 'atmosphere', 'tone', 'plot', 'characters', 'style')
    ranked: list[tuple[str, float]] = []
    seen: set[str] = set()
    for category in category_order:
        for name, score in (dominant_feature_names({category: features.get(category, {})}, 0.12, 2).get(category) or []):
            key = normalize_label(name)
            if key and key not in seen:
                seen.add(key)
                ranked.append((name, round(float(score), 6)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return [name for name, _score in ranked[:limit]]


def get_matchable_text(book: dict[str, Any] | None) -> str:
    metadata = metadata_dict(book)
    passport = semantic_passport(book)
    parts = [
        str((book or {}).get('title') or ''),
        get_author(book),
        ' '.join(get_genres(book)),
        ' '.join(get_moods(book)),
        get_summary(book),
        ' '.join(get_theme_hints(book)),
        ' '.join(as_list(passport.get('entities'))[:8]),
        ' '.join(as_list(passport.get('synthetic_queries'))[:8]),
        str(passport.get('search_text') or ''),
        str(metadata.get('description') or ''),
    ]
    return ' '.join(part for part in parts if part).strip()
