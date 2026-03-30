# Архитектура data-backed MVP

## Сервисы

### 1. Go backend
Отвечает за product API и stateful product flow:
- auth/register/login/refresh/me;
- onboarding;
- interactions;
- recommendation feed orchestration;
- explain endpoint;
- reading lists;
- PostgreSQL persistence;
- Redis caching/invalidation.

### 2. Python recommendation service
Отвечает за AI/recommendation слой:
- reuse исходного search/index/feature extraction ядра;
- ingest txt/fb2/pdf/epub/zip;
- hybrid search;
- taste-profile;
- ranking;
- explainability;
- optional Qdrant sync.

## Поток запроса

### Register / Login
Client -> Go backend -> PostgreSQL (`users`, `auth_accounts`, `refresh_tokens`)

### Onboarding
Client -> Go backend -> PostgreSQL (`onboarding_state`, `user_profiles`) -> Redis invalidation -> Python recommendation service -> PostgreSQL profile snapshot update -> Redis profile cache

### Recommendation feed
Client -> Go backend -> Redis feed cache lookup -> Python recommendation service -> PostgreSQL `recommendation_events` -> Redis feed cache

### Interaction
Client -> Go backend -> PostgreSQL `user_interactions` (+ optional `reading_lists` / `reading_list_items`) -> Redis invalidation -> Python recommendation service -> PostgreSQL `user_profiles` update snapshot -> Redis session/profile state

## Почему так

- Product state остаётся в Go backend и PostgreSQL/Redis.
- Recommendation logic остаётся в Python, где уже есть сильное существующее ядро.
- Нет полного rewrite Python-проекта.
- Нет premature microservices explosion.
- Архитектура масштабируется: позже можно вынести worker, async jobs и shared event bus.

## PostgreSQL

Минимально используются таблицы:
- `users`
- `auth_accounts`
- `refresh_tokens`
- `onboarding_state`
- `user_profiles`
- `user_interactions`
- `reading_lists`
- `reading_list_items`
- `recommendation_events`
- `session_state`

## Redis

Используется для:
- recommendation cache;
- profile cache;
- onboarding/session state cache;
- cache invalidation после onboarding/interactions.

## Qdrant

- `book_feature_vectors` — feature vectors книг.
- `user_taste_vectors` — user taste vectors.

Если Qdrant не доступен, recommendation service использует fallback candidate generation поверх search engine.
