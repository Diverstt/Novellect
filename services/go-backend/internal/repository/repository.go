package repository

import (
	"context"
	"encoding/json"

	"novellect/go-backend/internal/model"
)

type Repository interface {
	Ping(ctx context.Context) error
	CreateUser(ctx context.Context, user model.User) (model.User, error)
	FindUserByEmail(ctx context.Context, email string) (model.User, bool, error)
	GetUser(ctx context.Context, userID string) (model.User, bool, error)
	UpdateUserPasswordHash(ctx context.Context, userID, passwordHash string, updatedAt int64) error
	SaveRefreshToken(ctx context.Context, token model.RefreshToken) error
	FindRefreshToken(ctx context.Context, tokenHash string) (model.RefreshToken, bool, error)
	RevokeRefreshToken(ctx context.Context, tokenHash string, revokedAt int64) error
	SaveOnboarding(ctx context.Context, state model.OnboardingState) error
	GetOnboarding(ctx context.Context, userID string) (model.OnboardingState, bool, error)
	SaveUserProfile(ctx context.Context, profile model.UserProfile) error
	GetUserProfile(ctx context.Context, userID string) (model.UserProfile, bool, error)
	AppendInteraction(ctx context.Context, event model.Interaction) error
	ListInteractions(ctx context.Context, userID string, limit int) ([]model.Interaction, error)
	EnsureReadingList(ctx context.Context, list model.ReadingList) (model.ReadingList, error)
	GetReadingList(ctx context.Context, readingListID string) (model.ReadingList, bool, error)
	AddReadingListItem(ctx context.Context, item model.ReadingListItem) error
	ListReadingLists(ctx context.Context, userID string) ([]model.ReadingList, error)
	ListReadingListItems(ctx context.Context, readingListID string) ([]model.ReadingListItem, error)
	SaveRecommendationEvent(ctx context.Context, event model.RecommendationEvent) error
	FindRecommendationExplanation(ctx context.Context, userID, bookID, recommendationContext string, limit int) (json.RawMessage, bool, error)
	UpsertSessionState(ctx context.Context, state model.SessionState) error
	GetSessionState(ctx context.Context, sessionKey string) (model.SessionState, bool, error)
}
