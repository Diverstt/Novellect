package model

import "encoding/json"

type User struct {
	ID           string `json:"id"`
	Email        string `json:"email"`
	DisplayName  string `json:"display_name"`
	PasswordHash string `json:"-"`
	CreatedAt    int64  `json:"created_at"`
	UpdatedAt    int64  `json:"updated_at"`
}
type RefreshToken struct {
	ID         string
	UserID     string
	TokenHash  string
	UserAgent  string
	RemoteAddr string
	CreatedAt  int64
	ExpiresAt  int64
	RevokedAt  int64
}
type OnboardingState struct {
	UserID    string         `json:"user_id"`
	Payload   map[string]any `json:"payload"`
	Completed bool           `json:"completed"`
	UpdatedAt int64          `json:"updated_at"`
}
type UserProfile struct {
	UserID           string         `json:"user_id"`
	Onboarding       map[string]any `json:"onboarding"`
	Profile          map[string]any `json:"profile"`
	LastRecomputedAt int64          `json:"last_recomputed_at"`
	CreatedAt        int64          `json:"created_at"`
	UpdatedAt        int64          `json:"updated_at"`
}
type Interaction struct {
	ID        string         `json:"id"`
	UserID    string         `json:"user_id"`
	BookID    string         `json:"book_id"`
	Action    string         `json:"action"`
	SessionID string         `json:"session_id"`
	Source    string         `json:"source"`
	Metadata  map[string]any `json:"metadata"`
	CreatedAt int64          `json:"created_at"`
}
type ReadingList struct {
	ID        string `json:"id"`
	UserID    string `json:"user_id"`
	Name      string `json:"name"`
	Kind      string `json:"kind"`
	CreatedAt int64  `json:"created_at"`
}
type ReadingListItem struct {
	ReadingListID string `json:"reading_list_id"`
	BookID        string `json:"book_id"`
	Position      int    `json:"position"`
	CreatedAt     int64  `json:"created_at"`
}
type RecommendationEvent struct {
	ID         string          `json:"id"`
	UserID     string          `json:"user_id"`
	SessionID  string          `json:"session_id"`
	Mode       string          `json:"mode"`
	SeedBookID string          `json:"seed_book_id"`
	Request    json.RawMessage `json:"request_json"`
	Response   json.RawMessage `json:"response_json"`
	CreatedAt  int64           `json:"created_at"`
}
type SessionState struct {
	SessionKey string         `json:"session_key"`
	UserID     string         `json:"user_id"`
	State      map[string]any `json:"state"`
	UpdatedAt  int64          `json:"updated_at"`
}
