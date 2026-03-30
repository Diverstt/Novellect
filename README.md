# Novellect v2 — data-backed MVP

Этот архив переводит исходный Python-проект про книги из режима локального retrieval/demo в рабочий data-backed MVP:

- **Go backend** — product API, auth, sessions, interactions, reading lists, recommendation orchestration.
- **Python recommendation service** — reuse существующего ядра поиска/индексации/feature extraction и generation explainable recommendations. Теперь feed учитывает onboarding + live query + feedback, а локальная LLM используется только как внутренний AI-layer для query understanding и explanations.
- **Next.js web frontend** — интерфейс для auth, onboarding, feed и favorites поверх Go API.
- **PostgreSQL** — durable storage для users, refresh tokens, profiles, interactions, reading lists, recommendation events.
- **Redis** — recommendation cache, session cache, onboarding/session state cache, cache invalidation.
- **Qdrant** — опциональный vector store для книг и taste vectors.

## Что реально работает

### Go backend

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `GET /api/v1/me`
- `POST /api/v1/onboarding`
- `GET /api/v1/profile`
- `POST /api/v1/interactions`
- `GET /api/v1/recommendations/feed`
- `GET /api/v1/recommendations/explain`
- `GET /api/v1/reading-lists`
- `POST /api/v1/reading-lists`
- `POST /api/v1/reading-lists/{listID}/items`
- `POST /api/v1/search/query`
- `GET /healthz`

### Python recommendation service

- `GET /healthz`
- `GET /api/v1/library/books`
- `GET /api/v1/library/books/{book_id}`
- `POST /api/v1/library/ingest/text`
- `POST /api/v1/search/query`
- `GET /api/v1/profile/{user_id}`
- `POST /api/v1/profile/{user_id}/onboarding`
- `POST /api/v1/interactions/ingest`
- `POST /api/v1/recommendations/feed`
- `GET /api/v1/recommendations/feed/{user_id}`
- `GET /api/v1/books/{book_id}/preview`
- `GET /api/v1/books/{book_id}/content`
- `POST /api/v1/admin/qdrant/sync`

### Web frontend

- `/` — landing page
- `/register`
- `/login`
- `/onboarding`
- `/feed`
- `/favorites`
- `/api/v1/*` — proxy к Go backend без CORS-настроек на клиенте

## Быстрый старт

1. Скопируйте пример окружения:

```bash
cp .env.example .env
```

2. Поднимите проект:

```bash
docker compose up --build
```

3. Проверьте health:

```bash
curl http://localhost:8080/healthz
curl http://localhost:8000/healthz
```

Откройте интерфейс:

```text
http://localhost:3000
```

Локальная модель по умолчанию ожидается как внутренний сервис с моделью `qwen2.5:14b-instruct`. Python service ходит к нему по `NOVELLECT_LLM_SERVICE_URL`; наружу модель не публикуется. При timeout / invalid JSON / пустом ответе сервис деградирует в deterministic fallback без поломки feed.

4. Прогоните smoke flow:

```bash
bash scripts/e2e_smoke.sh
```

## Runbook

### Поднять стек

```bash
docker compose up -d postgres redis qdrant python-recommendation python-ingestion-worker go-backend web-frontend --no-build
```

Проверка:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8080/healthz
curl http://localhost:6333/collections/book_semantic_vectors
```

UI доступен на `http://localhost:3000`, pgAdmin на `http://localhost:5050`.

### Принудительно досинхронизировать каталог в Qdrant

```bash
curl -X POST http://localhost:8000/api/v1/admin/qdrant/sync
```

### Чистая переиндексация mounted dataset

Эта процедура пересобирает локальный Python runtime и Qdrant с нуля, не трогая PostgreSQL и Redis:

```bash
docker compose stop go-backend python-recommendation python-ingestion-worker qdrant
docker run --rm -v "$PWD/dataset:/dataset-host" novellect_v2_pgredis_dataset_ingestion_20260326-python-recommendation sh -lc 'cp -an /dataset-host/processed/. /dataset-host/inbox/ && rm -rf /dataset-host/processed/* /dataset-host/manifests/* /dataset-host/failed/*'
docker compose rm -f go-backend python-recommendation python-ingestion-worker qdrant
docker volume rm -f novellect_v2_pgredis_dataset_ingestion_20260326_python_service_data novellect_v2_pgredis_dataset_ingestion_20260326_qdrant_data
docker compose up -d qdrant python-recommendation python-ingestion-worker go-backend --no-build
```

Следить за прогрессом:

```bash
find dataset/inbox -type f | wc -l
find dataset/processed -type f | wc -l
docker compose logs --tail=50 python-ingestion-worker
```

### Что должно получиться после ingestion

- `dataset/inbox` пуст
- `dataset/processed` содержит весь текущий набор книг
- `GET /healthz` у Python service показывает актуальный `books_indexed`
- `book_semantic_vectors` в Qdrant содержит столько же `points`, сколько книг в индексе

### Важно про fallback embeddings

Если `sentence-transformers` недоступен в контейнере, сервис не падает в нулевые векторы:

- chunk embeddings строятся как детерминированные lexical hash embeddings размерности `384`
- Qdrant остаётся рабочим как recall layer
- при появлении нормальной модели можно просто сделать чистую переиндексацию и заменить fallback на полноценную семантику

## Локальный запуск без Docker

### Python recommendation service

```bash
cd services/python-recommendation
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Опциональные пакеты для старого Streamlit UI и тяжёлых моделей:

```bash
pip install -r requirements-optional.txt
```

### Go backend

```bash
cd services/go-backend
go run ./cmd/server
```

Перед запуском нужны PostgreSQL и Redis. Переменные смотрите в `.env.example`.

### Web frontend

```bash
cd services/web-frontend
npm install
npm run dev
```

По умолчанию UI поднимается на `http://localhost:3000` и проксирует `/api/v1/*` в Go backend.

## Пример end-to-end через curl

### 1. Register

```bash
curl -s -X POST http://localhost:8080/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"reader@example.com","password":"secret12","display_name":"Reader"}'
```

### 2. Login

```bash
curl -s -X POST http://localhost:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"reader@example.com","password":"secret12"}'
```

### 3. Seed a few books into the Python service

```bash
curl -s -X POST http://localhost:8000/api/v1/library/ingest/text \
  -H 'Content-Type: application/json' \
  -d '{"title":"Тайна старого дома","content":"Мрачная усадьба, тайна исчезновения семьи, тревожная атмосфера, расследование и секреты.","file_id":"book_1"}'
```

```bash
curl -s -X POST http://localhost:8000/api/v1/library/ingest/text \
  -H 'Content-Type: application/json' \
  -d '{"title":"Светлая дорога","content":"Путешествие, дружба, надежда, теплая атмосфера и преодоление трудностей.","file_id":"book_2"}'
```

### 4. Onboarding

```bash
curl -s -X POST 'http://localhost:8080/api/v1/onboarding' \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"favorite_moods":["тревожное"],"favorite_atmosphere":["таинственная","мрачная"],"favorite_plot":["тайна"],"favorite_books":["Тайна старого дома"]}'
```

### 5. Feed

```bash
curl -s 'http://localhost:8080/api/v1/recommendations/feed?mode=similar&limit=5&session_id=session-1' \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

### 6. Interaction

```bash
curl -s -X POST http://localhost:8080/api/v1/interactions \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"book_id":"book_1","action":"like","session_id":"session-1","source":"swipe"}'
```

### 7. Updated feed

```bash
curl -s 'http://localhost:8080/api/v1/recommendations/feed?mode=similar&limit=5&session_id=session-1' \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

### 8. Explain recommendation

```bash
curl -s 'http://localhost:8080/api/v1/recommendations/explain?book_id=book_1&session_id=session-1' \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```
