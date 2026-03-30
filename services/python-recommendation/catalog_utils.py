from __future__ import annotations

from typing import Any

from search_engine import TEXT_FEATURES, load_index


def feature_key_order() -> list[str]:
    return [f'{category}.{subcat}' for category, subcats in TEXT_FEATURES.items() for subcat in subcats]


def empty_feature_map() -> dict[str, float]:
    return {key: 0.0 for key in feature_key_order()}


def inflate_feature_scores(flattened: dict[str, float] | None) -> dict[str, dict[str, float]]:
    tree: dict[str, dict[str, float]] = {category: {} for category in TEXT_FEATURES}
    for full_key, value in (flattened or {}).items():
        if '.' not in full_key:
            continue
        category, feature = full_key.split('.', 1)
        tree.setdefault(category, {})[feature] = round(float(value), 6)
    return tree


def flatten_feature_scores(feature_tree: dict[str, dict[str, float]] | None) -> dict[str, float]:
    flattened = empty_feature_map()
    for category, values in (feature_tree or {}).items():
        for feature_name, score in (values or {}).items():
            flattened[f'{category}.{feature_name}'] = round(float(score), 6)
    return flattened


def feature_vector(flattened: dict[str, float] | None) -> list[float]:
    flat = flattened or {}
    return [round(float(flat.get(key, 0.0)), 6) for key in feature_key_order()]


def cosine_similarity_from_maps(left: dict[str, float] | None, right: dict[str, float] | None) -> float:
    vector_left = feature_vector(left)
    vector_right = feature_vector(right)
    numerator = sum(a * b for a, b in zip(vector_left, vector_right))
    norm_left = sum(a * a for a in vector_left) ** 0.5
    norm_right = sum(a * a for a in vector_right) ** 0.5
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return round(float(numerator / (norm_left * norm_right)), 6)


def book_feature_map(book: dict[str, Any] | None) -> dict[str, float]:
    flattened = empty_feature_map()
    for category, values in ((book or {}).get('features') or {}).items():
        for feature_name, score in (values or {}).items():
            flattened[f'{category}.{feature_name}'] = round(float(score), 6)
    return flattened


def feature_priorities_from_flattened(flattened: dict[str, float] | None, threshold: float = 0.1, limit_per_group: int = 2) -> dict[str, list[tuple[str, float]]]:
    priorities: dict[str, list[tuple[str, float]]] = {category: [] for category in TEXT_FEATURES}
    for full_key, score in (flattened or {}).items():
        if '.' not in full_key:
            continue
        category, feature_name = full_key.split('.', 1)
        numeric = round(float(score), 6)
        if numeric >= threshold:
            priorities.setdefault(category, []).append((feature_name, numeric))
    for category in list(priorities.keys()):
        priorities[category].sort(key=lambda item: item[1], reverse=True)
        priorities[category] = priorities[category][:limit_per_group]
    return priorities


def dominant_feature_names(feature_tree: dict[str, dict[str, float]] | None, threshold: float = 0.1, limit_per_group: int = 2) -> dict[str, list[tuple[str, float]]]:
    result: dict[str, list[tuple[str, float]]] = {}
    for category, values in (feature_tree or {}).items():
        scored = [(name, round(float(score), 6)) for name, score in (values or {}).items() if float(score) >= threshold]
        if scored:
            result[category] = sorted(scored, key=lambda item: item[1], reverse=True)[:limit_per_group]
    return result


def feature_richness(book: dict[str, Any] | None) -> float:
    return round(sum(max(0.0, value) for value in book_feature_map(book).values()), 6)


def book_index_by_id() -> dict[str, dict[str, Any]]:
    return {str(item.get('id')): item for item in load_index(force=True)}
