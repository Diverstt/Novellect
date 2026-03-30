from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException

from api_models import HealthResponse, InteractionRequest, OnboardingRequest, RecommendationRequest, SearchRequest, TextIngestRequest
from recommendation_service import RecommendationService

logging.basicConfig(
    level=getattr(logging, str(os.getenv('NOVELLECT_LOG_LEVEL', 'INFO')).upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)

app = FastAPI(title='Novellect Recommendation Service', version='0.4.0')
service = RecommendationService()


@app.on_event('startup')
def startup_event():
    service.start_background_tasks()


@app.on_event('shutdown')
def shutdown_event():
    service.stop_background_tasks()


@app.get('/healthz', response_model=HealthResponse)
def healthz():
    return service.health()


@app.get('/api/v1/library/books')
def list_books():
    items = service.list_books()
    return {'items': items, 'count': len(items)}


@app.get('/api/v1/library/books/{book_id}')
def get_book(book_id: str):
    book = service.get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail='Book not found')
    return book


@app.get('/api/v1/library/books/{book_id}/preview')
@app.get('/api/v1/books/{book_id}/preview')
def get_book_preview(book_id: str):
    preview = service.get_book_preview(book_id)
    if preview is None:
        raise HTTPException(status_code=404, detail='Book not found')
    return preview


@app.get('/api/v1/library/books/{book_id}/content')
@app.get('/api/v1/books/{book_id}/content')
def get_book_content(book_id: str):
    payload = service.get_book_content(book_id)
    if payload is None:
        raise HTTPException(status_code=404, detail='Book not found')
    return payload


@app.post('/api/v1/library/ingest/text')
def ingest_text_endpoint(request: TextIngestRequest):
    return service.ingest_text(request.title, request.content, request.metadata, request.file_id)


@app.post('/api/v1/search/query')
def search_query(request: SearchRequest):
    return service.run_search(request.query)


@app.get('/api/v1/profile/{user_id}')
def get_profile(user_id: str, session_id: str | None = None):
    return service.get_profile(user_id, session_id)


@app.post('/api/v1/profile/{user_id}/onboarding')
def save_onboarding(user_id: str, request: OnboardingRequest):
    payload = request.model_dump() if hasattr(request, 'model_dump') else request.dict()
    return service.apply_onboarding(user_id, payload)


@app.post('/api/v1/interactions/ingest')
def ingest_interaction(request: InteractionRequest):
    try:
        return service.ingest_interaction(request.user_id, request.book_id, request.action, request.session_id, request.source, request.metadata)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/v1/recommendations/feed')
def recommendation_feed(request: RecommendationRequest):
    return service.get_recommendations(
        request.user_id,
        request.mode,
        request.limit,
        request.session_id,
        request.seed_book_id,
        request.query,
        request.exclude_book_ids,
        request.cursor,
        request.offset,
        request.recommendation_context,
        request.search_context,
    )


@app.get('/api/v1/recommendations/feed/{user_id}')
def recommendation_feed_get(user_id: str, mode: str = 'similar', limit: int = 10, session_id: str | None = None, seed_book_id: str | None = None, query: str | None = None, exclude_book_ids: str | None = None, cursor: str | None = None, offset: int | None = None, recommendation_context: str | None = None, search_context: str | None = None):
    excludes = [item.strip() for item in str(exclude_book_ids or '').split(',') if item.strip()]
    return service.get_recommendations(user_id, mode, limit, session_id, seed_book_id, query, excludes, cursor, offset, recommendation_context, search_context)


@app.post('/api/v1/admin/qdrant/sync')
def sync_qdrant():
    return service.sync_catalog_to_qdrant()
