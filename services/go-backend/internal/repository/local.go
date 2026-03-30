package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"novellect/go-backend/internal/model"
)

type LocalRepository struct {
	path string
	mu   sync.RWMutex
	data localRepositoryState
}

type localRepositoryState struct {
	Users                map[string]model.User                  `json:"users"`
	UserIDsByEmail       map[string]string                      `json:"user_ids_by_email"`
	RefreshTokens        map[string]model.RefreshToken          `json:"refresh_tokens"`
	Onboarding           map[string]model.OnboardingState       `json:"onboarding"`
	Profiles             map[string]model.UserProfile           `json:"profiles"`
	Interactions         map[string][]model.Interaction         `json:"interactions"`
	ReadingLists         map[string]model.ReadingList           `json:"reading_lists"`
	ReadingListItems     map[string][]model.ReadingListItem     `json:"reading_list_items"`
	RecommendationEvents map[string][]model.RecommendationEvent `json:"recommendation_events"`
	Sessions             map[string]model.SessionState          `json:"sessions"`
}

func NewLocalRepository(path string) (*LocalRepository, error) {
	repo := &LocalRepository{path: path}
	repo.data.ensureMaps()
	if err := repo.load(); err != nil {
		return nil, err
	}
	return repo, nil
}

func (r *LocalRepository) Ping(context.Context) error { return nil }

func (r *LocalRepository) CreateUser(_ context.Context, user model.User) (model.User, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	email := normalizeEmail(user.Email)
	if email == "" {
		return model.User{}, errors.New("email required")
	}
	if existingID := r.data.UserIDsByEmail[email]; existingID != "" {
		return model.User{}, errors.New("user already exists")
	}
	user.Email = email
	if user.CreatedAt == 0 {
		user.CreatedAt = nowUnix()
	}
	if user.UpdatedAt == 0 {
		user.UpdatedAt = user.CreatedAt
	}
	r.data.Users[user.ID] = cloneUser(user)
	r.data.UserIDsByEmail[email] = user.ID
	if err := r.saveLocked(); err != nil {
		return model.User{}, err
	}
	return cloneUser(user), nil
}

func (r *LocalRepository) FindUserByEmail(_ context.Context, email string) (model.User, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	userID := r.data.UserIDsByEmail[normalizeEmail(email)]
	if userID == "" {
		return model.User{}, false, nil
	}
	user, ok := r.data.Users[userID]
	if !ok {
		return model.User{}, false, nil
	}
	return cloneUser(user), true, nil
}

func (r *LocalRepository) GetUser(_ context.Context, userID string) (model.User, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	user, ok := r.data.Users[userID]
	if !ok {
		return model.User{}, false, nil
	}
	return cloneUser(user), true, nil
}

func (r *LocalRepository) UpdateUserPasswordHash(_ context.Context, userID, passwordHash string, updatedAt int64) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	user, ok := r.data.Users[userID]
	if !ok {
		return errors.New("user not found")
	}
	user.PasswordHash = passwordHash
	user.UpdatedAt = updatedAt
	r.data.Users[userID] = user
	return r.saveLocked()
}

func (r *LocalRepository) SaveRefreshToken(_ context.Context, token model.RefreshToken) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.data.RefreshTokens[token.TokenHash] = cloneRefreshToken(token)
	return r.saveLocked()
}

func (r *LocalRepository) FindRefreshToken(_ context.Context, tokenHash string) (model.RefreshToken, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	token, ok := r.data.RefreshTokens[tokenHash]
	if !ok {
		return model.RefreshToken{}, false, nil
	}
	return cloneRefreshToken(token), true, nil
}

func (r *LocalRepository) RevokeRefreshToken(_ context.Context, tokenHash string, revokedAt int64) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	token, ok := r.data.RefreshTokens[tokenHash]
	if !ok {
		return nil
	}
	token.RevokedAt = revokedAt
	r.data.RefreshTokens[tokenHash] = token
	return r.saveLocked()
}

func (r *LocalRepository) SaveOnboarding(_ context.Context, state model.OnboardingState) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	state.Payload = cloneMap(state.Payload)
	r.data.Onboarding[state.UserID] = state

	profile := r.data.Profiles[state.UserID]
	if profile.UserID == "" {
		profile = model.UserProfile{UserID: state.UserID, CreatedAt: state.UpdatedAt}
	}
	profile.Onboarding = cloneMap(state.Payload)
	profile.UpdatedAt = state.UpdatedAt
	if profile.LastRecomputedAt == 0 {
		profile.LastRecomputedAt = state.UpdatedAt
	}
	r.data.Profiles[state.UserID] = cloneUserProfile(profile)
	return r.saveLocked()
}

func (r *LocalRepository) GetOnboarding(_ context.Context, userID string) (model.OnboardingState, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	state, ok := r.data.Onboarding[userID]
	if !ok {
		return model.OnboardingState{}, false, nil
	}
	state.Payload = cloneMap(state.Payload)
	return state, true, nil
}

func (r *LocalRepository) SaveUserProfile(_ context.Context, profile model.UserProfile) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	if profile.CreatedAt == 0 {
		profile.CreatedAt = nowUnix()
	}
	if profile.UpdatedAt == 0 {
		profile.UpdatedAt = profile.CreatedAt
	}
	r.data.Profiles[profile.UserID] = cloneUserProfile(profile)
	return r.saveLocked()
}

func (r *LocalRepository) GetUserProfile(_ context.Context, userID string) (model.UserProfile, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	profile, ok := r.data.Profiles[userID]
	if !ok {
		return model.UserProfile{}, false, nil
	}
	return cloneUserProfile(profile), true, nil
}

func (r *LocalRepository) AppendInteraction(_ context.Context, event model.Interaction) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.data.Interactions[event.UserID] = append(r.data.Interactions[event.UserID], cloneInteraction(event))
	return r.saveLocked()
}

func (r *LocalRepository) ListInteractions(_ context.Context, userID string, limit int) ([]model.Interaction, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	items := append([]model.Interaction(nil), r.data.Interactions[userID]...)
	sort.Slice(items, func(i, j int) bool {
		return items[i].CreatedAt > items[j].CreatedAt
	})
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}
	for i := range items {
		items[i] = cloneInteraction(items[i])
	}
	return items, nil
}

func (r *LocalRepository) EnsureReadingList(_ context.Context, list model.ReadingList) (model.ReadingList, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	for _, existing := range r.data.ReadingLists {
		if existing.UserID == list.UserID && existing.Kind == list.Kind && list.Kind != "" {
			return cloneReadingList(existing), nil
		}
	}
	r.data.ReadingLists[list.ID] = cloneReadingList(list)
	if err := r.saveLocked(); err != nil {
		return model.ReadingList{}, err
	}
	return cloneReadingList(list), nil
}

func (r *LocalRepository) GetReadingList(_ context.Context, readingListID string) (model.ReadingList, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	list, ok := r.data.ReadingLists[readingListID]
	if !ok {
		return model.ReadingList{}, false, nil
	}
	return cloneReadingList(list), true, nil
}

func (r *LocalRepository) AddReadingListItem(_ context.Context, item model.ReadingListItem) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	items := append([]model.ReadingListItem(nil), r.data.ReadingListItems[item.ReadingListID]...)
	replaced := false
	for idx := range items {
		if items[idx].BookID == item.BookID {
			items[idx] = cloneReadingListItem(item)
			replaced = true
			break
		}
	}
	if !replaced {
		items = append(items, cloneReadingListItem(item))
	}
	r.data.ReadingListItems[item.ReadingListID] = items
	return r.saveLocked()
}

func (r *LocalRepository) ListReadingLists(_ context.Context, userID string) ([]model.ReadingList, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	items := make([]model.ReadingList, 0)
	for _, list := range r.data.ReadingLists {
		if list.UserID == userID {
			items = append(items, cloneReadingList(list))
		}
	}
	sort.Slice(items, func(i, j int) bool {
		return items[i].CreatedAt > items[j].CreatedAt
	})
	return items, nil
}

func (r *LocalRepository) ListReadingListItems(_ context.Context, readingListID string) ([]model.ReadingListItem, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	items := append([]model.ReadingListItem(nil), r.data.ReadingListItems[readingListID]...)
	sort.Slice(items, func(i, j int) bool {
		if items[i].Position == items[j].Position {
			return items[i].CreatedAt < items[j].CreatedAt
		}
		return items[i].Position < items[j].Position
	})
	for i := range items {
		items[i] = cloneReadingListItem(items[i])
	}
	return items, nil
}

func (r *LocalRepository) SaveRecommendationEvent(_ context.Context, event model.RecommendationEvent) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.data.RecommendationEvents[event.UserID] = append(r.data.RecommendationEvents[event.UserID], cloneRecommendationEvent(event))
	return r.saveLocked()
}

func (r *LocalRepository) FindRecommendationExplanation(_ context.Context, userID, bookID, recommendationContext string, limit int) (json.RawMessage, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	items := r.data.RecommendationEvents[userID]
	if limit <= 0 || limit > len(items) {
		limit = len(items)
	}
	for idx := len(items) - 1; idx >= 0 && len(items)-idx <= limit; idx-- {
		var payload map[string]any
		if json.Unmarshal(items[idx].Response, &payload) != nil {
			continue
		}
		raw, ok := recommendationExplanationForBook(payload, bookID, recommendationContext)
		if ok {
			return raw, true, nil
		}
	}
	return nil, false, nil
}

func (r *LocalRepository) UpsertSessionState(_ context.Context, state model.SessionState) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.data.Sessions[state.SessionKey] = cloneSessionState(state)
	return r.saveLocked()
}

func (r *LocalRepository) GetSessionState(_ context.Context, sessionKey string) (model.SessionState, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	state, ok := r.data.Sessions[sessionKey]
	if !ok {
		return model.SessionState{}, false, nil
	}
	return cloneSessionState(state), true, nil
}

func (r *LocalRepository) load() error {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.data.ensureMaps()
	if r.path == "" {
		return nil
	}
	raw, err := os.ReadFile(r.path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if len(raw) == 0 {
		return nil
	}
	if err := json.Unmarshal(raw, &r.data); err != nil {
		return err
	}
	r.data.ensureMaps()
	return nil
}

func (r *LocalRepository) saveLocked() error {
	r.data.ensureMaps()
	if r.path == "" {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(r.path), 0o755); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(r.data, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(r.path, raw, 0o644)
}

func (s *localRepositoryState) ensureMaps() {
	if s.Users == nil {
		s.Users = map[string]model.User{}
	}
	if s.UserIDsByEmail == nil {
		s.UserIDsByEmail = map[string]string{}
	}
	if s.RefreshTokens == nil {
		s.RefreshTokens = map[string]model.RefreshToken{}
	}
	if s.Onboarding == nil {
		s.Onboarding = map[string]model.OnboardingState{}
	}
	if s.Profiles == nil {
		s.Profiles = map[string]model.UserProfile{}
	}
	if s.Interactions == nil {
		s.Interactions = map[string][]model.Interaction{}
	}
	if s.ReadingLists == nil {
		s.ReadingLists = map[string]model.ReadingList{}
	}
	if s.ReadingListItems == nil {
		s.ReadingListItems = map[string][]model.ReadingListItem{}
	}
	if s.RecommendationEvents == nil {
		s.RecommendationEvents = map[string][]model.RecommendationEvent{}
	}
	if s.Sessions == nil {
		s.Sessions = map[string]model.SessionState{}
	}
}

func cloneMap(value map[string]any) map[string]any {
	if value == nil {
		return map[string]any{}
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return map[string]any{}
	}
	cloned := map[string]any{}
	if json.Unmarshal(raw, &cloned) != nil {
		return map[string]any{}
	}
	return cloned
}

func cloneUser(user model.User) model.User { return user }

func cloneRefreshToken(token model.RefreshToken) model.RefreshToken { return token }

func cloneUserProfile(profile model.UserProfile) model.UserProfile {
	profile.Onboarding = cloneMap(profile.Onboarding)
	profile.Profile = cloneMap(profile.Profile)
	return profile
}

func cloneInteraction(event model.Interaction) model.Interaction {
	event.Metadata = cloneMap(event.Metadata)
	return event
}

func cloneReadingList(list model.ReadingList) model.ReadingList { return list }

func cloneReadingListItem(item model.ReadingListItem) model.ReadingListItem { return item }

func cloneRecommendationEvent(event model.RecommendationEvent) model.RecommendationEvent {
	event.Request = append(json.RawMessage(nil), event.Request...)
	event.Response = append(json.RawMessage(nil), event.Response...)
	return event
}

func cloneSessionState(state model.SessionState) model.SessionState {
	state.State = cloneMap(state.State)
	return state
}

func recommendationExplanationForBook(payload map[string]any, bookID, recommendationContext string) (json.RawMessage, bool) {
	payloadContext := strings.TrimSpace(fmt.Sprint(payload["recommendation_context"]))
	if recommendationContext != "" && payloadContext != "" && payloadContext != recommendationContext {
		return nil, false
	}
	items, _ := payload["items"].([]any)
	for _, item := range items {
		itemMap, _ := item.(map[string]any)
		itemContext := strings.TrimSpace(fmt.Sprint(itemMap["recommendation_context"]))
		if recommendationContext != "" && itemContext != "" && itemContext != recommendationContext {
			continue
		}
		if fmt.Sprint(itemMap["book_id"]) == bookID {
			raw, _ := json.Marshal(itemMap)
			return json.RawMessage(raw), true
		}
	}
	return nil, false
}
