from __future__ import annotations

import re
from typing import Any

from book_metadata import get_author, get_genres, get_matchable_text, get_moods, get_style_tags, get_summary, get_theme_hints, normalize_label
from llm_runtime import generate_json
from search_engine import get_query_analyzer

ALLOWED_INTENTS = {'recommendation', 'comparative_recommendation', 'title_lookup', 'author_lookup', 'keyword_search'}
LIST_FIELDS = ('genres', 'themes', 'moods', 'tone', 'comparables', 'constraints', 'avoid')


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item).strip()
        key = normalize_label(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _as_clean_list(value: Any, limit: int = 6) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = re.split(r'[,;|/]+', str(value))
    return _unique([str(item).strip() for item in raw_items])[:limit]


def _heuristic_query_understanding(query: str) -> dict[str, Any]:
    analysis = get_query_analyzer().analyze_query(query or '')
    priorities = analysis.get('priorities') or {}
    lowered = normalize_label(query)
    genres = []
    genre_hints = {
        'детектив': ['детектив'],
        'триллер': ['триллер'],
        'фэнтези': ['фэнтези', 'fantasy'],
        'фантаст': ['фантастика', 'sci-fi'],
        'роман': ['роман', 'отношения'],
        'драма': ['драма'],
        'хоррор': ['хоррор', 'ужас'],
        'готик': ['готика'],
        'историч': ['исторический роман'],
    }
    for probe, values in genre_hints.items():
        if probe in lowered:
            genres.extend(values)
    moods = [name for name, _score in priorities.get('mood', [])]
    tone = [name for name, _score in priorities.get('tone', [])]
    themes = [name for name, _score in priorities.get('plot', [])] + [name for name, _score in priorities.get('atmosphere', [])]
    semantic_theme_hints = {
        'пороки человечества': ['пороки человечества', 'вина и раскаяние', 'бедность и унижение'],
        'моральное падение': ['вина и раскаяние', 'пороки человечества'],
        'вина': ['вина и раскаяние'],
        'раскаяние': ['вина и раскаяние'],
        'ревизские сказки': ['бюрократия и афера', 'социальная сатира'],
        'утопил собаку': ['жестокость и сострадание'],
        'процентщицу': ['вина и раскаяние', 'пороки человечества'],
    }
    for probe, values in semantic_theme_hints.items():
        if probe in lowered:
            themes.extend(values)
    if 'ревизск' in lowered and ('сказ' in lowered or 'душ' in lowered):
        themes.extend(['бюрократия и афера', 'социальная сатира'])

    comparables = []
    for raw in re.findall(r'(?:как|в духе|похоже на)\s+([^,.;!?]+)', query or '', flags=re.IGNORECASE):
        cleaned = str(raw).strip(' "\'')
        if cleaned and len(cleaned.split()) <= 6:
            comparables.append(cleaned)

    constraints = []
    for probe, label in (
        ('коротк', 'короткая'),
        ('быстр', 'динамичная'),
        ('легк', 'легкая'),
        ('сильн', 'сильный сюжет'),
        ('атмосфер', 'атмосферная'),
    ):
        if probe in lowered:
            constraints.append(label)
    if 'утоп' in lowered and 'собак' in lowered:
        themes.extend(['жестокость и сострадание'])
        constraints.append('утопление')

    avoid = []
    if 'без' in lowered:
        for probe, label in (
            ('романтик', 'романтика'),
            ('детектив', 'детектив'),
            ('маг', 'магия'),
            ('мистик', 'мистика'),
            ('насили', 'насилие'),
            ('жест', 'жестокость'),
            ('политик', 'политика'),
        ):
            if probe in lowered:
                avoid.append(label)

    language = 'ru' if any('а' <= ch <= 'я' or ch == 'ё' for ch in lowered) else 'unknown'
    confidence = 0.45
    if moods or tone or themes or genres or comparables or constraints:
        confidence = 0.72

    intent = 'recommendation'
    if any(marker in lowered for marker in ('что почитать', 'посоветуй', 'подбери', 'хочу')):
        intent = 'recommendation'
    elif any(marker in lowered for marker in ('похоже на', 'как у', 'в духе')):
        intent = 'comparative_recommendation'

    return {
        'intent': intent,
        'genres': _unique(genres),
        'themes': _unique(themes),
        'moods': _unique(moods),
        'tone': _unique(tone),
        'comparables': _unique(comparables),
        'constraints': _unique(constraints),
        'avoid': _unique(avoid),
        'language': language,
        'audience': '',
        'confidence': round(confidence, 3),
    }


def _normalize_query_payload(loaded: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result = dict(fallback)
    intent = str(loaded.get('intent') or '').strip().lower()
    if intent in ALLOWED_INTENTS:
        result['intent'] = intent
    for key in LIST_FIELDS:
        candidate_items = _as_clean_list(loaded.get(key))
        if candidate_items:
            result[key] = candidate_items
    language = str(loaded.get('language') or '').strip().lower()
    if language:
        result['language'] = language[:12]
    audience = str(loaded.get('audience') or '').strip()
    if audience:
        result['audience'] = audience[:80]
    try:
        result['confidence'] = round(max(0.0, min(float(loaded.get('confidence', result['confidence'])), 1.0)), 3)
    except Exception:
        pass
    return result


def understand_query(query: str) -> dict[str, Any]:
    fallback = _heuristic_query_understanding(query)
    if float(fallback.get('confidence', 0.0)) >= 0.65:
        return fallback
    prompt = (
        '### TASK: UNDERSTAND_QUERY ###\n'
        'Верни только один JSON-объект без markdown и пояснений.\n'
        'Если сигнал неясен, оставь пустой список или пустую строку.\n'
        '{"intent":"","genres":[],"themes":[],"moods":[],"tone":[],"comparables":[],"constraints":[],"avoid":[],"language":"","audience":"","confidence":0.0}\n'
        f'QUERY: {str(query or "").strip()[:500]}\n'
    )
    loaded = generate_json(prompt, max_new_tokens=96, temperature=0.0)
    if not isinstance(loaded, dict):
        return fallback
    return _normalize_query_payload(loaded, fallback)


def query_reason_candidates(book: dict[str, Any], query_understanding: dict[str, Any] | None) -> list[str]:
    if not query_understanding:
        return []
    reasons: list[str] = []
    book_tokens = normalize_label(get_matchable_text(book))
    for key in ('constraints', 'themes', 'genres', 'moods', 'tone'):
        for item in query_understanding.get(key) or []:
            label = normalize_label(item)
            if label and label in book_tokens:
                reasons.append(item)
    return _unique(reasons)


def query_match_score(book: dict[str, Any], query_understanding: dict[str, Any] | None, query: str | None = None) -> float:
    score = 0.0
    if not book:
        return 0.0
    matchable = normalize_label(get_matchable_text(book))
    for item in query_reason_candidates(book, query_understanding):
        score += 0.18
    for item in (query_understanding or {}).get('constraints') or []:
        label = normalize_label(item)
        if label and label in matchable:
            score += 0.09
    if query:
        for token in normalize_label(query).split():
            if len(token) > 3 and token in matchable:
                score += 0.03
    for item in (query_understanding or {}).get('avoid') or []:
        label = normalize_label(item)
        if label and label in matchable:
            score -= 0.28
    return round(max(-1.0, min(score, 1.0)), 6)


def _fallback_explanation_payload(book: dict[str, Any], reason_candidates: list[str], score_breakdown: dict[str, Any] | None, profile_summary: dict[str, Any] | None, recommendation_context: str) -> dict[str, Any]:
    reasons = _unique(reason_candidates)
    if recommendation_context == 'query':
        if not reasons:
            reasons = _unique(get_theme_hints(book)[:2] + get_genres(book)[:2] + get_moods(book)[:2])[:3]
        why = 'Книга частично совпадает с тем, что ты ищешь прямо сейчас.'
        if reasons:
            why = 'Книга подходит под текущий запрос, потому что в ней есть ' + ', '.join(reasons[:3]) + '.'
        caveats = []
        if score_breakdown and float(score_breakdown.get('query_penalty', 0.0)) > 0.0:
            caveats.append('есть элементы, которые расходятся с ограничениями запроса')
        return {
            'why_for_you': why,
            'match_reasons': reasons[:4],
            'caveats': caveats[:2],
            'style_tags': get_style_tags(book)[:4],
            'explanation_mode': 'query_explanation',
        }

    if not reasons:
        top_reasons: list[str] = []
        for source in (get_genres(book), get_moods(book), get_theme_hints(book), get_style_tags(book)):
            top_reasons.extend(source[:2])
        reasons = _unique(top_reasons)[:3]
    why = 'Книга подходит под общий профиль твоего чтения.'
    if reasons:
        why = 'Книга может понравиться, если тебе близки ' + ', '.join(reasons[:3]) + '.'
    caveats = []
    if score_breakdown and float(score_breakdown.get('negative_similarity', 0.0)) > 0.25:
        caveats.append('есть пересечение с тем, что вы раньше отвергали')
    if score_breakdown and float(score_breakdown.get('exploration_bonus', 0.0)) > 0.4:
        caveats.append('это более исследовательская рекомендация')
    return {
        'why_for_you': why,
        'match_reasons': reasons[:4],
        'caveats': caveats[:2],
        'style_tags': get_style_tags(book)[:4],
        'explanation_mode': 'profile_explanation',
    }


def _normalize_explanation_payload(loaded: dict[str, Any], fallback: dict[str, Any], allowed_reasons: list[str], allowed_tags: list[str]) -> dict[str, Any]:
    payload = dict(fallback)
    why = str(loaded.get('why_for_you') or '').strip()
    if why and len(why) <= 320 and '###' not in why:
        payload['why_for_you'] = why

    allowed_reason_keys = {normalize_label(value) for value in allowed_reasons}
    allowed_tag_keys = {normalize_label(value) for value in allowed_tags}

    payload['match_reasons'] = _unique([
        item for item in _as_clean_list(loaded.get('match_reasons'), limit=4) if normalize_label(item) in allowed_reason_keys
    ])[:4] or payload['match_reasons']
    payload['style_tags'] = _unique([
        item for item in _as_clean_list(loaded.get('style_tags'), limit=4) if normalize_label(item) in allowed_tag_keys
    ])[:4] or payload['style_tags']
    payload['caveats'] = _as_clean_list(loaded.get('caveats'), limit=2)[:2]
    return payload


def generate_card_explanation(book: dict[str, Any], query: str | None, query_understanding: dict[str, Any] | None, score_breakdown: dict[str, Any] | None, reason_candidates: list[str], profile_summary: dict[str, Any] | None, recommendation_context: str = 'profile', use_llm: bool = True) -> dict[str, Any]:
    fallback = _fallback_explanation_payload(book, reason_candidates, score_breakdown, profile_summary, recommendation_context)
    if not use_llm:
        return fallback

    if recommendation_context == 'query':
        allowed_reasons = _unique(reason_candidates + get_theme_hints(book)[:2] + get_genres(book)[:2] + get_moods(book)[:2])[:8]
    else:
        allowed_reasons = _unique(reason_candidates + get_genres(book)[:2] + get_moods(book)[:2] + get_theme_hints(book)[:2] + get_style_tags(book)[:4])[:8]
    allowed_tags = _unique(get_style_tags(book)[:6] + get_theme_hints(book)[:4])[:6]
    prompt = (
        '### TASK: CARD_EXPLANATION ###\n'
        'Верни только один JSON-объект. Не выдумывай факты и не добавляй неуказанные подробности.\n'
        '{"why_for_you":"","match_reasons":[],"caveats":[],"style_tags":[]}\n'
        f'TITLE: {book.get("title") or ""}\n'
        f'AUTHOR: {get_author(book)}\n'
        f'GENRES: {", ".join(get_genres(book))}\n'
        f'MOODS: {", ".join(get_moods(book))}\n'
        f'SUMMARY: {get_summary(book)}\n'
        f'RECOMMENDATION_CONTEXT: {recommendation_context}\n'
        f'QUERY: {query or ""}\n'
        f'QUERY_SIGNALS: {query_understanding or {}}\n'
        f'ALLOWED_REASONS: {allowed_reasons}\n'
        f'ALLOWED_STYLE_TAGS: {allowed_tags}\n'
        f'SCORE_HINTS: {score_breakdown or {}}\n'
        f'PROFILE_HINTS: {profile_summary if recommendation_context == "profile" else {}}\n'
    )
    loaded = generate_json(prompt, max_new_tokens=64, temperature=0.05)
    if not isinstance(loaded, dict):
        return fallback
    return _normalize_explanation_payload(loaded, fallback, allowed_reasons, allowed_tags)
