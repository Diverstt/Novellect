from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    books_indexed: int
    qdrant_enabled: bool
    profile_store_backend: str | None = None


class TextIngestRequest(BaseModel):
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    file_id: str | None = None


class SearchRequest(BaseModel):
    query: str


class OnboardingRequest(BaseModel):
    favorite_moods: list[str] = Field(default_factory=list)
    favorite_atmosphere: list[str] = Field(default_factory=list)
    favorite_tone: list[str] = Field(default_factory=list)
    favorite_style: list[str] = Field(default_factory=list)
    favorite_plot: list[str] = Field(default_factory=list)
    favorite_books: list[str] = Field(default_factory=list)
    favorite_genres: list[str] = Field(default_factory=list)
    favorite_authors: list[str] = Field(default_factory=list)
    avoid_tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class InteractionRequest(BaseModel):
    user_id: str
    book_id: str
    action: Literal['like', 'dislike', 'skip', 'save', 'swipe_left', 'swipe_right']
    session_id: str | None = None
    source: str = 'api'
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecommendationRequest(BaseModel):
    user_id: str
    mode: Literal['similar', 'new', 'risk'] = 'similar'
    search_context: Literal['profile', 'query'] | None = None
    recommendation_context: Literal['profile', 'query'] | None = None
    limit: int = 10
    session_id: str | None = None
    seed_book_id: str | None = None
    query: str | None = None
    exclude_book_ids: list[str] = Field(default_factory=list)
    cursor: str | None = None
    offset: int | None = None
