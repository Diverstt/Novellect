package repository

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"time"

	"novellect/go-backend/internal/model"
	"novellect/go-backend/internal/pgclient"
)

type PostgresRepository struct{ client *pgclient.Client }

func NewPostgresRepository(client *pgclient.Client) *PostgresRepository {
	return &PostgresRepository{client: client}
}

func normalizeEmail(email string) string { return strings.ToLower(strings.TrimSpace(email)) }
func parseInt64(raw string) int64 {
	value, _ := strconv.ParseInt(strings.TrimSpace(raw), 10, 64)
	return value
}
func parseInt(raw string) int { value, _ := strconv.Atoi(strings.TrimSpace(raw)); return value }
func parseJSONMap(raw string) map[string]any {
	if strings.TrimSpace(raw) == "" {
		return map[string]any{}
	}
	var payload map[string]any
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return map[string]any{}
	}
	return payload
}
func nowUnix() int64 { return time.Now().Unix() }
func scanUser(rows [][]string) (model.User, bool) {
	if len(rows) == 0 {
		return model.User{}, false
	}
	row := rows[0]
	return model.User{ID: row[0], Email: row[1], DisplayName: row[2], PasswordHash: row[3], CreatedAt: parseInt64(row[4]), UpdatedAt: parseInt64(row[5])}, true
}

func (r *PostgresRepository) Ping(ctx context.Context) error { return r.client.Ping(ctx) }

func (r *PostgresRepository) CreateUser(ctx context.Context, user model.User) (model.User, error) {
	user.Email = normalizeEmail(user.Email)
	if user.CreatedAt == 0 {
		user.CreatedAt = nowUnix()
	}
	if user.UpdatedAt == 0 {
		user.UpdatedAt = user.CreatedAt
	}
	if err := r.client.ExecParams(
		ctx,
		"INSERT INTO users (id,email,display_name,password_hash,created_at,updated_at) VALUES ($1,$2,$3,$4,$5,$6);",
		user.ID, user.Email, user.DisplayName, user.PasswordHash, user.CreatedAt, user.UpdatedAt,
	); err != nil {
		return model.User{}, err
	}
	if err := r.client.ExecParams(
		ctx,
		"INSERT INTO auth_accounts (id,user_id,provider,provider_subject,created_at) VALUES ($1,$2,$3,$4,$5);",
		"acc_"+user.ID, user.ID, "password", user.Email, user.CreatedAt,
	); err != nil {
		return model.User{}, err
	}
	return user, nil
}

func (r *PostgresRepository) FindUserByEmail(ctx context.Context, email string) (model.User, bool, error) {
	result, err := r.client.QueryParams(ctx, "SELECT id,email,display_name,password_hash,created_at,updated_at FROM users WHERE email=$1 LIMIT 1;", normalizeEmail(email))
	if err != nil {
		return model.User{}, false, err
	}
	user, ok := scanUser(result.Rows)
	return user, ok, nil
}

func (r *PostgresRepository) GetUser(ctx context.Context, userID string) (model.User, bool, error) {
	result, err := r.client.QueryParams(ctx, "SELECT id,email,display_name,password_hash,created_at,updated_at FROM users WHERE id=$1 LIMIT 1;", userID)
	if err != nil {
		return model.User{}, false, err
	}
	user, ok := scanUser(result.Rows)
	return user, ok, nil
}

func (r *PostgresRepository) UpdateUserPasswordHash(ctx context.Context, userID, passwordHash string, updatedAt int64) error {
	return r.client.ExecParams(ctx, "UPDATE users SET password_hash=$1, updated_at=$2 WHERE id=$3;", passwordHash, updatedAt, userID)
}

func (r *PostgresRepository) SaveRefreshToken(ctx context.Context, token model.RefreshToken) error {
	return r.client.ExecParams(
		ctx,
		"INSERT INTO refresh_tokens (id,user_id,token_hash,user_agent,remote_addr,created_at,expires_at,revoked_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8);",
		token.ID, token.UserID, token.TokenHash, token.UserAgent, token.RemoteAddr, token.CreatedAt, token.ExpiresAt, token.RevokedAt,
	)
}

func (r *PostgresRepository) FindRefreshToken(ctx context.Context, tokenHash string) (model.RefreshToken, bool, error) {
	result, err := r.client.QueryParams(ctx, "SELECT id,user_id,token_hash,user_agent,remote_addr,created_at,expires_at,revoked_at FROM refresh_tokens WHERE token_hash=$1 LIMIT 1;", tokenHash)
	if err != nil {
		return model.RefreshToken{}, false, err
	}
	if len(result.Rows) == 0 {
		return model.RefreshToken{}, false, nil
	}
	row := result.Rows[0]
	return model.RefreshToken{ID: row[0], UserID: row[1], TokenHash: row[2], UserAgent: row[3], RemoteAddr: row[4], CreatedAt: parseInt64(row[5]), ExpiresAt: parseInt64(row[6]), RevokedAt: parseInt64(row[7])}, true, nil
}

func (r *PostgresRepository) RevokeRefreshToken(ctx context.Context, tokenHash string, revokedAt int64) error {
	return r.client.ExecParams(ctx, "UPDATE refresh_tokens SET revoked_at=$1 WHERE token_hash=$2;", revokedAt, tokenHash)
}

func (r *PostgresRepository) SaveOnboarding(ctx context.Context, state model.OnboardingState) error {
	if err := r.client.ExecParams(
		ctx,
		"INSERT INTO onboarding_state (user_id,payload,completed,updated_at) VALUES ($1,$2,$3,$4) ON CONFLICT (user_id) DO UPDATE SET payload=EXCLUDED.payload, completed=EXCLUDED.completed, updated_at=EXCLUDED.updated_at;",
		state.UserID, pgclient.JSONB(state.Payload), state.Completed, state.UpdatedAt,
	); err != nil {
		return err
	}
	profile, ok, err := r.GetUserProfile(ctx, state.UserID)
	if err != nil {
		return err
	}
	if !ok {
		profile = model.UserProfile{UserID: state.UserID, CreatedAt: state.UpdatedAt}
	}
	profile.Onboarding = state.Payload
	profile.UpdatedAt = state.UpdatedAt
	if profile.LastRecomputedAt == 0 {
		profile.LastRecomputedAt = state.UpdatedAt
	}
	return r.SaveUserProfile(ctx, profile)
}

func (r *PostgresRepository) GetOnboarding(ctx context.Context, userID string) (model.OnboardingState, bool, error) {
	result, err := r.client.QueryParams(ctx, "SELECT user_id,COALESCE(payload::text,'{}'),completed,updated_at FROM onboarding_state WHERE user_id=$1 LIMIT 1;", userID)
	if err != nil {
		return model.OnboardingState{}, false, err
	}
	if len(result.Rows) == 0 {
		return model.OnboardingState{}, false, nil
	}
	row := result.Rows[0]
	return model.OnboardingState{UserID: row[0], Payload: parseJSONMap(row[1]), Completed: row[2] == "t" || row[2] == "true", UpdatedAt: parseInt64(row[3])}, true, nil
}

func (r *PostgresRepository) SaveUserProfile(ctx context.Context, profile model.UserProfile) error {
	if profile.CreatedAt == 0 {
		profile.CreatedAt = nowUnix()
	}
	if profile.UpdatedAt == 0 {
		profile.UpdatedAt = profile.CreatedAt
	}
	return r.client.ExecParams(
		ctx,
		"INSERT INTO user_profiles (user_id,onboarding_json,profile_json,last_recomputed_at,created_at,updated_at) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (user_id) DO UPDATE SET onboarding_json=EXCLUDED.onboarding_json, profile_json=EXCLUDED.profile_json, last_recomputed_at=EXCLUDED.last_recomputed_at, updated_at=EXCLUDED.updated_at;",
		profile.UserID, pgclient.JSONB(profile.Onboarding), pgclient.JSONB(profile.Profile), profile.LastRecomputedAt, profile.CreatedAt, profile.UpdatedAt,
	)
}

func (r *PostgresRepository) GetUserProfile(ctx context.Context, userID string) (model.UserProfile, bool, error) {
	result, err := r.client.QueryParams(ctx, "SELECT user_id,COALESCE(onboarding_json::text,'{}'),COALESCE(profile_json::text,'{}'),last_recomputed_at,created_at,updated_at FROM user_profiles WHERE user_id=$1 LIMIT 1;", userID)
	if err != nil {
		return model.UserProfile{}, false, err
	}
	if len(result.Rows) == 0 {
		return model.UserProfile{}, false, nil
	}
	row := result.Rows[0]
	return model.UserProfile{UserID: row[0], Onboarding: parseJSONMap(row[1]), Profile: parseJSONMap(row[2]), LastRecomputedAt: parseInt64(row[3]), CreatedAt: parseInt64(row[4]), UpdatedAt: parseInt64(row[5])}, true, nil
}

func (r *PostgresRepository) AppendInteraction(ctx context.Context, event model.Interaction) error {
	return r.client.ExecParams(
		ctx,
		"INSERT INTO user_interactions (id,user_id,book_id,action,session_id,source,metadata,created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8);",
		event.ID, event.UserID, event.BookID, event.Action, event.SessionID, event.Source, pgclient.JSONB(event.Metadata), event.CreatedAt,
	)
}

func (r *PostgresRepository) ListInteractions(ctx context.Context, userID string, limit int) ([]model.Interaction, error) {
	if limit <= 0 {
		limit = 50
	}
	result, err := r.client.QueryParams(ctx, "SELECT id,user_id,book_id,action,session_id,source,COALESCE(metadata::text,'{}'),created_at FROM user_interactions WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2;", userID, limit)
	if err != nil {
		return nil, err
	}
	items := make([]model.Interaction, 0, len(result.Rows))
	for _, row := range result.Rows {
		items = append(items, model.Interaction{ID: row[0], UserID: row[1], BookID: row[2], Action: row[3], SessionID: row[4], Source: row[5], Metadata: parseJSONMap(row[6]), CreatedAt: parseInt64(row[7])})
	}
	return items, nil
}

func scanReadingList(rows [][]string) (model.ReadingList, bool) {
	if len(rows) == 0 {
		return model.ReadingList{}, false
	}
	row := rows[0]
	return model.ReadingList{ID: row[0], UserID: row[1], Name: row[2], Kind: row[3], CreatedAt: parseInt64(row[4])}, true
}

func (r *PostgresRepository) EnsureReadingList(ctx context.Context, list model.ReadingList) (model.ReadingList, error) {
	result, err := r.client.QueryParams(ctx, "SELECT id,user_id,name,kind,created_at FROM reading_lists WHERE user_id=$1 AND kind=$2 LIMIT 1;", list.UserID, list.Kind)
	if err != nil {
		return model.ReadingList{}, err
	}
	if existing, ok := scanReadingList(result.Rows); ok {
		return existing, nil
	}
	if err := r.client.ExecParams(ctx, "INSERT INTO reading_lists (id,user_id,name,kind,created_at) VALUES ($1,$2,$3,$4,$5);", list.ID, list.UserID, list.Name, list.Kind, list.CreatedAt); err != nil {
		return model.ReadingList{}, err
	}
	return list, nil
}

func (r *PostgresRepository) GetReadingList(ctx context.Context, readingListID string) (model.ReadingList, bool, error) {
	result, err := r.client.QueryParams(ctx, "SELECT id,user_id,name,kind,created_at FROM reading_lists WHERE id=$1 LIMIT 1;", readingListID)
	if err != nil {
		return model.ReadingList{}, false, err
	}
	list, ok := scanReadingList(result.Rows)
	return list, ok, nil
}

func (r *PostgresRepository) AddReadingListItem(ctx context.Context, item model.ReadingListItem) error {
	return r.client.ExecParams(
		ctx,
		"INSERT INTO reading_list_items (reading_list_id,book_id,position,created_at) VALUES ($1,$2,$3,$4) ON CONFLICT (reading_list_id,book_id) DO UPDATE SET position=EXCLUDED.position;",
		item.ReadingListID, item.BookID, item.Position, item.CreatedAt,
	)
}

func (r *PostgresRepository) ListReadingLists(ctx context.Context, userID string) ([]model.ReadingList, error) {
	result, err := r.client.QueryParams(ctx, "SELECT id,user_id,name,kind,created_at FROM reading_lists WHERE user_id=$1 ORDER BY created_at DESC;", userID)
	if err != nil {
		return nil, err
	}
	items := make([]model.ReadingList, 0, len(result.Rows))
	for _, row := range result.Rows {
		items = append(items, model.ReadingList{ID: row[0], UserID: row[1], Name: row[2], Kind: row[3], CreatedAt: parseInt64(row[4])})
	}
	return items, nil
}

func (r *PostgresRepository) ListReadingListItems(ctx context.Context, readingListID string) ([]model.ReadingListItem, error) {
	result, err := r.client.QueryParams(ctx, "SELECT reading_list_id,book_id,position,created_at FROM reading_list_items WHERE reading_list_id=$1 ORDER BY position ASC, created_at ASC;", readingListID)
	if err != nil {
		return nil, err
	}
	items := make([]model.ReadingListItem, 0, len(result.Rows))
	for _, row := range result.Rows {
		items = append(items, model.ReadingListItem{ReadingListID: row[0], BookID: row[1], Position: parseInt(row[2]), CreatedAt: parseInt64(row[3])})
	}
	return items, nil
}

func (r *PostgresRepository) SaveRecommendationEvent(ctx context.Context, event model.RecommendationEvent) error {
	return r.client.ExecParams(
		ctx,
		"INSERT INTO recommendation_events (id,user_id,session_id,mode,seed_book_id,request_json,response_json,created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8);",
		event.ID, event.UserID, event.SessionID, event.Mode, event.SeedBookID, pgclient.JSONB(json.RawMessage(event.Request)), pgclient.JSONB(json.RawMessage(event.Response)), event.CreatedAt,
	)
}

func (r *PostgresRepository) FindRecommendationExplanation(ctx context.Context, userID, bookID, recommendationContext string, limit int) (json.RawMessage, bool, error) {
	if limit <= 0 {
		limit = 10
	}
	result, err := r.client.QueryParams(ctx, "SELECT COALESCE(response_json::text,'{}') FROM recommendation_events WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2;", userID, limit)
	if err != nil {
		return nil, false, err
	}
	for _, row := range result.Rows {
		var payload map[string]any
		if json.Unmarshal([]byte(row[0]), &payload) != nil {
			continue
		}
		raw, ok := recommendationExplanationForBook(payload, bookID, recommendationContext)
		if ok {
			return raw, true, nil
		}
	}
	return nil, false, nil
}

func (r *PostgresRepository) UpsertSessionState(ctx context.Context, state model.SessionState) error {
	return r.client.ExecParams(
		ctx,
		"INSERT INTO session_state (session_key,user_id,state_json,updated_at) VALUES ($1,$2,$3,$4) ON CONFLICT (session_key) DO UPDATE SET state_json=EXCLUDED.state_json, updated_at=EXCLUDED.updated_at;",
		state.SessionKey, state.UserID, pgclient.JSONB(state.State), state.UpdatedAt,
	)
}

func (r *PostgresRepository) GetSessionState(ctx context.Context, sessionKey string) (model.SessionState, bool, error) {
	result, err := r.client.QueryParams(ctx, "SELECT session_key,user_id,COALESCE(state_json::text,'{}'),updated_at FROM session_state WHERE session_key=$1 LIMIT 1;", sessionKey)
	if err != nil {
		return model.SessionState{}, false, err
	}
	if len(result.Rows) == 0 {
		return model.SessionState{}, false, nil
	}
	row := result.Rows[0]
	return model.SessionState{SessionKey: row[0], UserID: row[1], State: parseJSONMap(row[2]), UpdatedAt: parseInt64(row[3])}, true, nil
}
