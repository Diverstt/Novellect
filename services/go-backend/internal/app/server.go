package app

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"novellect/go-backend/internal/auth"
	"novellect/go-backend/internal/cache"
	"novellect/go-backend/internal/config"
	"novellect/go-backend/internal/httpx"
	"novellect/go-backend/internal/model"
	"novellect/go-backend/internal/recommendation"
	"novellect/go-backend/internal/repository"
)

type contextKey string

const userClaimsKey contextKey = "userClaims"

type Server struct {
	cfg          config.Config
	repo         repository.Repository
	cache        *cache.RedisCache
	tokens       *auth.TokenManager
	rec          *recommendation.Client
	logger       *log.Logger
	mux          *http.ServeMux
	feedMu       sync.Mutex
	feedInFlight map[string]*inFlightFeed
}
type inFlightFeed struct {
	done    chan struct{}
	payload map[string]any
	err     error
}
type statusResponseWriter struct {
	http.ResponseWriter
	status int
}
type userResponse struct {
	ID          string `json:"id"`
	Email       string `json:"email"`
	DisplayName string `json:"display_name"`
	CreatedAt   int64  `json:"created_at"`
}

func sanitizeUser(user model.User) userResponse {
	return userResponse{ID: user.ID, Email: user.Email, DisplayName: user.DisplayName, CreatedAt: user.CreatedAt}
}
func NewServer(cfg config.Config, repo repository.Repository, cache *cache.RedisCache, logger *log.Logger) *Server {
	if logger == nil {
		logger = log.New(log.Writer(), "novellect-go ", log.LstdFlags|log.Lmicroseconds)
	}
	s := &Server{
		cfg:          cfg,
		repo:         repo,
		cache:        cache,
		tokens:       auth.NewTokenManager(cfg.JWTSecret, cfg.AccessTTL),
		rec:          recommendation.NewClient(cfg.PythonBaseURL),
		logger:       logger,
		mux:          http.NewServeMux(),
		feedInFlight: map[string]*inFlightFeed{},
	}
	s.routes()
	return s
}
func (w *statusResponseWriter) WriteHeader(statusCode int) {
	w.status = statusCode
	w.ResponseWriter.WriteHeader(statusCode)
}
func (s *Server) loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		recorder := &statusResponseWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, r)
		s.logger.Printf(
			`{"event":"http_request","method":%q,"path":%q,"status":%d,"duration_ms":%d,"remote_addr":%q}`,
			r.Method,
			r.URL.Path,
			recorder.status,
			time.Since(started).Milliseconds(),
			r.RemoteAddr,
		)
	})
}
func (s *Server) Handler() http.Handler { return s.corsMiddleware(s.loggingMiddleware(s.mux)) }

func (s *Server) corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := strings.TrimSpace(r.Header.Get("Origin"))
		if allowedOrigin(origin) {
			headers := w.Header()
			headers.Set("Access-Control-Allow-Origin", origin)
			headers.Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
			headers.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			headers.Set("Vary", "Origin")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func allowedOrigin(origin string) bool {
	if origin == "" {
		return false
	}
	parsed, err := url.Parse(origin)
	if err != nil {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	return host == "localhost" || host == "127.0.0.1"
}
func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.handleHealth)
	s.mux.HandleFunc("POST /api/v1/auth/register", s.handleRegister)
	s.mux.HandleFunc("POST /api/v1/auth/login", s.handleLogin)
	s.mux.HandleFunc("POST /api/v1/auth/refresh", s.handleRefresh)
	s.mux.HandleFunc("GET /api/v1/me", s.requireAuth(s.handleMe))
	s.mux.HandleFunc("POST /api/v1/onboarding", s.requireAuth(s.handleOnboarding))
	s.mux.HandleFunc("GET /api/v1/profile", s.requireAuth(s.handleProfile))
	s.mux.HandleFunc("POST /api/v1/interactions", s.requireAuth(s.handleInteraction))
	s.mux.HandleFunc("GET /api/v1/recommendations/feed", s.requireAuth(s.handleFeed))
	s.mux.HandleFunc("GET /api/v1/recommendations/explain", s.requireAuth(s.handleExplain))
	s.mux.HandleFunc("GET /api/v1/books/{bookID}/preview", s.requireAuth(s.handleBookPreview))
	s.mux.HandleFunc("GET /api/v1/books/{bookID}/content", s.requireAuth(s.handleBookContent))
	s.mux.HandleFunc("GET /api/v1/reading-lists", s.requireAuth(s.handleListReadingLists))
	s.mux.HandleFunc("POST /api/v1/reading-lists", s.requireAuth(s.handleCreateReadingList))
	s.mux.HandleFunc("POST /api/v1/reading-lists/{listID}/items", s.requireAuth(s.handleAddReadingListItem))
	s.mux.HandleFunc("POST /api/v1/search/query", s.handleSearchProxy)
}
func bearerToken(header string) string {
	if header == "" {
		return ""
	}
	parts := strings.SplitN(header, " ", 2)
	if len(parts) == 2 && strings.EqualFold(parts[0], "Bearer") {
		return strings.TrimSpace(parts[1])
	}
	return strings.TrimSpace(header)
}
func (s *Server) requireAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token := bearerToken(r.Header.Get("Authorization"))
		if token == "" {
			httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": "missing bearer token"})
			return
		}
		claims, err := s.tokens.Parse(token)
		if err != nil {
			httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
			return
		}
		next(w, r.WithContext(context.WithValue(r.Context(), userClaimsKey, claims)))
	}
}
func claimsFromContext(ctx context.Context) (auth.Claims, error) {
	claims, ok := ctx.Value(userClaimsKey).(auth.Claims)
	if !ok {
		return auth.Claims{}, errors.New("missing auth claims")
	}
	return claims, nil
}
func randID(prefix string) string {
	buf := make([]byte, 12)
	_, _ = rand.Read(buf)
	return prefix + base64.RawURLEncoding.EncodeToString(buf)
}
func mapFromAny(value any) map[string]any {
	mapped, ok := value.(map[string]any)
	if !ok || mapped == nil {
		return map[string]any{}
	}
	return mapped
}
func cloneMap(value map[string]any) map[string]any {
	if value == nil {
		return nil
	}
	cloned := make(map[string]any, len(value))
	for key, item := range value {
		cloned[key] = item
	}
	return cloned
}
func profileCacheKey(userID, sessionID string) string {
	if sessionID == "" {
		sessionID = "global"
	}
	return fmt.Sprintf("rec:profile:%s:%s", userID, sessionID)
}
func normalizeRecommendationContext(raw, query string) string {
	_, recommendationContext, _ := resolveFeedContexts("", raw, query)
	return recommendationContext
}

func normalizeContextValue(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "profile", "query":
		return strings.ToLower(strings.TrimSpace(raw))
	default:
		return ""
	}
}

func resolveFeedContexts(rawSearchContext, rawRecommendationContext, query string) (string, string, string) {
	searchContext := normalizeContextValue(rawSearchContext)
	recommendationContext := normalizeContextValue(rawRecommendationContext)

	if searchContext == "" && recommendationContext != "" {
		searchContext = recommendationContext
	}
	if recommendationContext == "" && searchContext != "" {
		recommendationContext = searchContext
	}
	if searchContext == "" && recommendationContext == "" {
		if strings.TrimSpace(query) != "" {
			searchContext = "query"
			recommendationContext = "query"
		} else {
			searchContext = "profile"
			recommendationContext = "profile"
		}
	}
	if searchContext != "" && recommendationContext != "" && searchContext != recommendationContext {
		recommendationContext = searchContext
	}

	requestMode := "profile_only"
	if searchContext == "query" {
		requestMode = "query_only"
	} else if strings.TrimSpace(query) != "" {
		requestMode = "profile_with_query"
	}
	return searchContext, recommendationContext, requestMode
}
func feedCacheKey(userID, recommendationContext, mode, sessionID, seedBookID string, limit int, query string, exclude []string, cursor string, offset *int) string {
	if sessionID == "" {
		sessionID = "global"
	}
	if seedBookID == "" {
		seedBookID = "none"
	}
	query = strings.TrimSpace(query)
	if query == "" {
		query = "none"
	}
	excludeKey := strings.Join(exclude, ",")
	if excludeKey == "" {
		excludeKey = "none"
	}
	if cursor == "" {
		cursor = "none"
	}
	offsetKey := "none"
	if offset != nil {
		offsetKey = strconv.Itoa(*offset)
	}
	return fmt.Sprintf("rec:feed:%s:%s:%s:%s:%s:%d:%s:%s:%s:%s", userID, recommendationContext, mode, sessionID, seedBookID, limit, query, excludeKey, cursor, offsetKey)
}
func parseCSVValues(raw string) []string {
	values := []string{}
	for _, item := range strings.Split(raw, ",") {
		value := strings.TrimSpace(item)
		if value != "" {
			values = append(values, value)
		}
	}
	return values
}
func parseOptionalNonNegativeInt(raw string) *int {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	parsed, err := strconv.Atoi(raw)
	if err != nil || parsed < 0 {
		return nil
	}
	return &parsed
}
func resolvedRequestMode(raw any) string {
	value := strings.TrimSpace(fmt.Sprint(raw))
	if value == "" || value == "<nil>" {
		return ""
	}
	return value
}
func applyFeedResponseDefaults(payload map[string]any, searchContext, requestMode string) {
	if payload == nil {
		return
	}
	if normalizeContextValue(fmt.Sprint(payload["search_context"])) == "" {
		payload["search_context"] = searchContext
	}
	if resolvedRequestMode(payload["request_mode"]) == "" {
		payload["request_mode"] = requestMode
	}
}
func findFeedItemByBookID(items []any, bookID string) (map[string]any, bool) {
	for _, item := range items {
		itemMap, ok := item.(map[string]any)
		if !ok {
			continue
		}
		if fmt.Sprint(itemMap["book_id"]) == bookID {
			return itemMap, true
		}
	}
	return nil, false
}
func onboardingCacheKey(userID string) string { return "state:onboarding:" + userID }
func sessionCacheKey(userID, sessionID string) string {
	return fmt.Sprintf("state:session:%s:%s", userID, sessionID)
}
func (s *Server) invalidateUserCaches(ctx context.Context, userID string) {
	_ = s.cache.DeletePrefix(ctx, "rec:feed:"+userID+":")
	_ = s.cache.DeletePrefix(ctx, "rec:profile:"+userID+":")
}
func (s *Server) doFeedSingleFlight(key string, build func() (map[string]any, error)) (map[string]any, error, bool) {
	s.feedMu.Lock()
	if inFlight, ok := s.feedInFlight[key]; ok {
		s.feedMu.Unlock()
		<-inFlight.done
		return cloneMap(inFlight.payload), inFlight.err, true
	}
	inFlight := &inFlightFeed{done: make(chan struct{})}
	s.feedInFlight[key] = inFlight
	s.feedMu.Unlock()

	payload, err := build()

	s.feedMu.Lock()
	inFlight.payload = cloneMap(payload)
	inFlight.err = err
	delete(s.feedInFlight, key)
	close(inFlight.done)
	s.feedMu.Unlock()

	return cloneMap(payload), err, false
}
func (s *Server) issueTokens(ctx context.Context, user model.User, userAgent, remoteAddr string) (map[string]any, error) {
	access, err := s.tokens.Issue(user)
	if err != nil {
		return nil, err
	}
	refreshRaw, refreshHash, err := auth.NewOpaqueToken()
	if err != nil {
		return nil, err
	}
	refresh := model.RefreshToken{ID: randID("rt_"), UserID: user.ID, TokenHash: refreshHash, CreatedAt: time.Now().Unix(), ExpiresAt: time.Now().Add(s.cfg.RefreshTTL).Unix(), UserAgent: userAgent, RemoteAddr: remoteAddr}
	if err := s.repo.SaveRefreshToken(ctx, refresh); err != nil {
		return nil, err
	}
	return map[string]any{"access_token": access, "refresh_token": refreshRaw, "token_type": "Bearer", "expires_in": int(s.cfg.AccessTTL.Seconds())}, nil
}
func (s *Server) saveProfileSnapshot(ctx context.Context, userID string, onboarding map[string]any, profile map[string]any, sessionID string) error {
	existing, ok, err := s.repo.GetUserProfile(ctx, userID)
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	if !ok {
		existing = model.UserProfile{UserID: userID, CreatedAt: now}
	}
	if onboarding == nil || len(onboarding) == 0 {
		onboardingState, ok, _ := s.repo.GetOnboarding(ctx, userID)
		if ok {
			onboarding = onboardingState.Payload
		}
	}
	existing.Onboarding = onboarding
	existing.Profile = profile
	existing.LastRecomputedAt = now
	existing.UpdatedAt = now
	if err := s.repo.SaveUserProfile(ctx, existing); err != nil {
		return err
	}
	if len(profile) > 0 {
		_ = s.cache.SetJSON(ctx, profileCacheKey(userID, sessionID), map[string]any{"user_id": userID, "onboarding": onboarding, "taste_profile": profile}, s.cache.CacheTTL())
	}
	return nil
}
func (s *Server) updateSessionState(ctx context.Context, userID, sessionID string, payload map[string]any) {
	if sessionID == "" {
		return
	}
	payload["updated_at"] = time.Now().Unix()
	payload["session_id"] = sessionID
	state := model.SessionState{SessionKey: sessionCacheKey(userID, sessionID), UserID: userID, State: payload, UpdatedAt: time.Now().Unix()}
	_ = s.repo.UpsertSessionState(ctx, state)
	_ = s.cache.SetJSON(ctx, sessionCacheKey(userID, sessionID), payload, s.cache.StateTTL())
}
func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	payload := map[string]any{"status": "ok", "python_recommendation_url": s.cfg.PythonBaseURL}
	if err := s.repo.Ping(ctx); err != nil {
		payload["postgres"] = err.Error()
		payload["status"] = "degraded"
	} else {
		payload["postgres"] = "ok"
	}
	if err := s.cache.Ping(ctx); err != nil {
		payload["redis"] = err.Error()
		payload["status"] = "degraded"
	} else {
		payload["redis"] = "ok"
	}
	if health, err := s.rec.Health(); err != nil {
		payload["python_recommendation"] = err.Error()
		payload["status"] = "degraded"
	} else {
		payload["python_recommendation"] = health
	}
	code := http.StatusOK
	if payload["status"] != "ok" {
		code = http.StatusServiceUnavailable
	}
	httpx.WriteJSON(w, code, payload)
}
func (s *Server) handleRegister(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var req struct {
		Email       string `json:"email"`
		Password    string `json:"password"`
		DisplayName string `json:"display_name"`
	}
	if err := httpx.ReadJSON(r, &req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if strings.TrimSpace(req.Email) == "" || len(strings.TrimSpace(req.Password)) < 6 {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": "email and password>=6 required"})
		return
	}
	if _, exists, err := s.repo.FindUserByEmail(ctx, req.Email); err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	} else if exists {
		httpx.WriteJSON(w, http.StatusConflict, map[string]any{"error": "user already exists"})
		return
	}
	hash, err := auth.HashPassword(req.Password)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	now := time.Now().Unix()
	user := model.User{ID: randID("usr_"), Email: req.Email, DisplayName: strings.TrimSpace(req.DisplayName), PasswordHash: hash, CreatedAt: now, UpdatedAt: now}
	user, err = s.repo.CreateUser(ctx, user)
	if err != nil {
		httpx.WriteJSON(w, http.StatusConflict, map[string]any{"error": err.Error()})
		return
	}
	tokens, err := s.issueTokens(ctx, user, r.UserAgent(), r.RemoteAddr)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusCreated, map[string]any{"user": sanitizeUser(user), "tokens": tokens})
}
func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := httpx.ReadJSON(r, &req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	user, exists, err := s.repo.FindUserByEmail(ctx, req.Email)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if !exists || !auth.VerifyPassword(user.PasswordHash, req.Password) {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid credentials"})
		return
	}
	if auth.NeedsPasswordRehash(user.PasswordHash) {
		if newHash, hashErr := auth.HashPassword(req.Password); hashErr == nil {
			now := time.Now().Unix()
			user.PasswordHash = newHash
			user.UpdatedAt = now
			if err := s.repo.UpdateUserPasswordHash(ctx, user.ID, newHash, now); err != nil {
				s.logger.Printf(`{"level":"warn","event":"password_rehash_failed","user_id":"%s","error":%q}`, user.ID, err.Error())
			}
		} else {
			s.logger.Printf(`{"level":"warn","event":"password_rehash_failed","user_id":"%s","error":%q}`, user.ID, hashErr.Error())
		}
	}
	tokens, err := s.issueTokens(ctx, user, r.UserAgent(), r.RemoteAddr)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"user": sanitizeUser(user), "tokens": tokens})
}
func (s *Server) handleRefresh(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var req struct {
		RefreshToken string `json:"refresh_token"`
	}
	if err := httpx.ReadJSON(r, &req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	tokenHash := auth.HashOpaqueToken(strings.TrimSpace(req.RefreshToken))
	stored, exists, err := s.repo.FindRefreshToken(ctx, tokenHash)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if !exists || stored.RevokedAt > 0 || stored.ExpiresAt < time.Now().Unix() {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": "refresh token invalid or expired"})
		return
	}
	_ = s.repo.RevokeRefreshToken(ctx, tokenHash, time.Now().Unix())
	user, exists, err := s.repo.GetUser(ctx, stored.UserID)
	if err != nil || !exists {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": "user not found"})
		return
	}
	tokens, err := s.issueTokens(ctx, user, r.UserAgent(), r.RemoteAddr)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"user": sanitizeUser(user), "tokens": tokens})
}
func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	user, exists, err := s.repo.GetUser(r.Context(), claims.Sub)
	if err != nil || !exists {
		httpx.WriteJSON(w, http.StatusNotFound, map[string]any{"error": "user not found"})
		return
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"user": sanitizeUser(user)})
}
func (s *Server) handleOnboarding(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	payload := map[string]any{}
	if err := httpx.ReadJSON(r, &payload); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	state := model.OnboardingState{UserID: claims.Sub, Payload: payload, Completed: true, UpdatedAt: time.Now().Unix()}
	if err := s.repo.SaveOnboarding(r.Context(), state); err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	_ = s.cache.SetJSON(r.Context(), onboardingCacheKey(claims.Sub), state, s.cache.StateTTL())
	s.invalidateUserCaches(r.Context(), claims.Sub)
	remote, err := s.rec.SaveOnboarding(claims.Sub, payload)
	if err != nil {
		httpx.WriteJSON(w, http.StatusAccepted, map[string]any{"status": "saved_locally", "warning": err.Error(), "onboarding": state})
		return
	}
	_ = s.saveProfileSnapshot(r.Context(), claims.Sub, payload, remote, "")
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"status": "ok", "onboarding": state, "taste_profile": remote})
}
func (s *Server) handleProfile(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	sessionID := r.URL.Query().Get("session_id")
	cached := map[string]any{}
	if ok, _ := s.cache.GetJSON(r.Context(), profileCacheKey(claims.Sub, sessionID), &cached); ok {
		httpx.WriteJSON(w, http.StatusOK, cached)
		return
	}
	onboarding, onboardingOK, _ := s.repo.GetOnboarding(r.Context(), claims.Sub)
	remote, err := s.rec.GetProfile(claims.Sub, sessionID)
	if err == nil {
		_ = s.saveProfileSnapshot(r.Context(), claims.Sub, onboarding.Payload, remote, sessionID)
		payload := map[string]any{"user_id": claims.Sub, "onboarding": onboarding.Payload, "taste_profile": remote}
		_ = s.cache.SetJSON(r.Context(), profileCacheKey(claims.Sub, sessionID), payload, s.cache.CacheTTL())
		httpx.WriteJSON(w, http.StatusOK, payload)
		return
	}
	profile, ok, dbErr := s.repo.GetUserProfile(r.Context(), claims.Sub)
	if dbErr == nil && ok {
		payload := map[string]any{"user_id": claims.Sub, "onboarding": profile.Onboarding, "taste_profile": profile.Profile, "warning": err.Error()}
		_ = s.cache.SetJSON(r.Context(), profileCacheKey(claims.Sub, sessionID), payload, s.cache.CacheTTL())
		httpx.WriteJSON(w, http.StatusOK, payload)
		return
	}
	fallback := map[string]any{}
	if onboardingOK {
		fallback = onboarding.Payload
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"user_id": claims.Sub, "onboarding": fallback, "warning": err.Error()})
}
func (s *Server) handleInteraction(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	var req struct {
		BookID    string         `json:"book_id"`
		Action    string         `json:"action"`
		SessionID string         `json:"session_id"`
		Source    string         `json:"source"`
		Metadata  map[string]any `json:"metadata"`
	}
	if err := httpx.ReadJSON(r, &req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	now := time.Now().Unix()
	event := model.Interaction{ID: randID("evt_"), UserID: claims.Sub, BookID: req.BookID, Action: strings.ToLower(strings.TrimSpace(req.Action)), SessionID: req.SessionID, Source: req.Source, Metadata: req.Metadata, CreatedAt: now}
	if event.Source == "" {
		event.Source = "api"
	}
	if err := s.repo.AppendInteraction(r.Context(), event); err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if event.Action == "save" || event.Action == "like" {
		list, err := s.repo.EnsureReadingList(r.Context(), model.ReadingList{ID: randID("list_"), UserID: claims.Sub, Name: "Saved Books", Kind: "saved", CreatedAt: now})
		if err == nil {
			_ = s.repo.AddReadingListItem(r.Context(), model.ReadingListItem{ReadingListID: list.ID, BookID: event.BookID, Position: int(now % 100000), CreatedAt: now})
		}
	}
	s.invalidateUserCaches(r.Context(), claims.Sub)
	s.updateSessionState(r.Context(), claims.Sub, event.SessionID, map[string]any{"last_action": event.Action, "last_book_id": event.BookID, "source": event.Source})
	remote, err := s.rec.IngestInteraction(event)
	if err != nil {
		httpx.WriteJSON(w, http.StatusAccepted, map[string]any{"status": "saved_locally", "warning": err.Error(), "interaction": event})
		return
	}
	profile := mapFromAny(remote["profile"])
	onboarding, _, _ := s.repo.GetOnboarding(r.Context(), claims.Sub)
	_ = s.saveProfileSnapshot(r.Context(), claims.Sub, onboarding.Payload, profile, event.SessionID)
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"status": "ok", "interaction": event, "remote": remote})
}
func (s *Server) handleFeed(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	mode := r.URL.Query().Get("mode")
	if mode == "" {
		mode = "similar"
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 {
		limit = 10
	}
	sessionID := r.URL.Query().Get("session_id")
	seedBookID := r.URL.Query().Get("seed_book_id")
	query := strings.TrimSpace(r.URL.Query().Get("query"))
	searchContext, recommendationContext, requestMode := resolveFeedContexts(r.URL.Query().Get("search_context"), r.URL.Query().Get("recommendation_context"), query)
	cursor := strings.TrimSpace(r.URL.Query().Get("cursor"))
	offsetPtr := parseOptionalNonNegativeInt(r.URL.Query().Get("offset"))
	excludeBookIDs := parseCSVValues(r.URL.Query().Get("exclude_book_ids"))
	cacheKey := feedCacheKey(claims.Sub, recommendationContext, mode, sessionID, seedBookID, limit, query, excludeBookIDs, cursor, offsetPtr)
	cached := map[string]any{}
	if ok, _ := s.cache.GetJSON(r.Context(), cacheKey, &cached); ok {
		applyFeedResponseDefaults(cached, searchContext, requestMode)
		cached["cached"] = true
		httpx.WriteJSON(w, http.StatusOK, cached)
		return
	}
	payload, err, shared := s.doFeedSingleFlight(cacheKey, func() (map[string]any, error) {
		payload, err := s.rec.FetchFeed(claims.Sub, mode, searchContext, recommendationContext, limit, sessionID, seedBookID, query, excludeBookIDs, cursor, offsetPtr)
		if err != nil {
			return nil, err
		}
		applyFeedResponseDefaults(payload, searchContext, requestMode)
		payload["cached"] = false
		_ = s.cache.SetJSON(r.Context(), cacheKey, payload, s.cache.CacheTTL())
		onboarding, _, _ := s.repo.GetOnboarding(r.Context(), claims.Sub)
		_ = s.saveProfileSnapshot(r.Context(), claims.Sub, onboarding.Payload, mapFromAny(payload["profile"]), sessionID)
		requestJSON, _ := json.Marshal(map[string]any{"mode": mode, "search_context": searchContext, "recommendation_context": recommendationContext, "request_mode": requestMode, "limit": limit, "session_id": sessionID, "seed_book_id": seedBookID, "query": query, "exclude_book_ids": excludeBookIDs, "cursor": cursor, "offset": offsetPtr})
		responseJSON, _ := json.Marshal(payload)
		_ = s.repo.SaveRecommendationEvent(r.Context(), model.RecommendationEvent{ID: randID("rec_"), UserID: claims.Sub, SessionID: sessionID, Mode: mode, SeedBookID: seedBookID, Request: requestJSON, Response: responseJSON, CreatedAt: time.Now().Unix()})
		s.updateSessionState(r.Context(), claims.Sub, sessionID, map[string]any{"last_mode": mode, "last_search_context": searchContext, "last_recommendation_context": recommendationContext, "last_request_mode": requestMode, "seed_book_id": seedBookID, "limit": limit, "query": query, "cursor": cursor, "offset": offsetPtr})
		return payload, nil
	})
	if err != nil {
		httpx.WriteJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	applyFeedResponseDefaults(payload, searchContext, requestMode)
	payload["cached"] = false
	if shared {
		payload["coalesced"] = true
	}
	httpx.WriteJSON(w, http.StatusOK, payload)
}
func (s *Server) handleExplain(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	bookID := strings.TrimSpace(r.URL.Query().Get("book_id"))
	if bookID == "" {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": "book_id required"})
		return
	}
	mode := r.URL.Query().Get("mode")
	if mode == "" {
		mode = "similar"
	}
	sessionID := r.URL.Query().Get("session_id")
	seedBookID := r.URL.Query().Get("seed_book_id")
	query := strings.TrimSpace(r.URL.Query().Get("query"))
	searchContext, recommendationContext, requestMode := resolveFeedContexts(r.URL.Query().Get("search_context"), r.URL.Query().Get("recommendation_context"), query)
	if raw, ok, err := s.repo.FindRecommendationExplanation(r.Context(), claims.Sub, bookID, recommendationContext, 20); err == nil && ok {
		payload := map[string]any{}
		if json.Unmarshal(raw, &payload) == nil {
			httpx.WriteJSON(w, http.StatusOK, map[string]any{"book_id": bookID, "item": payload, "source": "recommendation_events", "search_context": searchContext, "recommendation_context": recommendationContext, "request_mode": requestMode})
			return
		}
	}
	excludeBookIDs := parseCSVValues(r.URL.Query().Get("exclude_book_ids"))
	payload, err := s.rec.FetchFeed(claims.Sub, mode, searchContext, recommendationContext, 20, sessionID, seedBookID, query, excludeBookIDs, "", nil)
	if err != nil {
		httpx.WriteJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	items, _ := payload["items"].([]any)
	if item, ok := findFeedItemByBookID(items, bookID); ok {
		httpx.WriteJSON(w, http.StatusOK, map[string]any{"book_id": bookID, "item": item, "source": "fresh_feed", "search_context": searchContext, "recommendation_context": recommendationContext, "request_mode": requestMode})
		return
	}
	httpx.WriteJSON(w, http.StatusNotFound, map[string]any{"error": "book not found in recommendation context"})
}
func (s *Server) handleBookPreview(w http.ResponseWriter, r *http.Request) {
	_, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	bookID := strings.TrimSpace(r.PathValue("bookID"))
	if bookID == "" {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": "bookID required"})
		return
	}
	payload, err := s.rec.BookPreview(bookID)
	if err != nil {
		httpx.WriteJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusOK, payload)
}
func (s *Server) handleBookContent(w http.ResponseWriter, r *http.Request) {
	_, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	bookID := strings.TrimSpace(r.PathValue("bookID"))
	if bookID == "" {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": "bookID required"})
		return
	}
	payload, err := s.rec.BookContent(bookID)
	if err != nil {
		httpx.WriteJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusOK, payload)
}
func (s *Server) handleListReadingLists(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	lists, err := s.repo.ListReadingLists(r.Context(), claims.Sub)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"items": lists, "count": len(lists)})
}
func (s *Server) handleCreateReadingList(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	var req struct {
		Name string `json:"name"`
		Kind string `json:"kind"`
	}
	if err := httpx.ReadJSON(r, &req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if strings.TrimSpace(req.Name) == "" {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": "name required"})
		return
	}
	if req.Kind == "" {
		req.Kind = "custom"
	}
	list, err := s.repo.EnsureReadingList(r.Context(), model.ReadingList{ID: randID("list_"), UserID: claims.Sub, Name: req.Name, Kind: req.Kind, CreatedAt: time.Now().Unix()})
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusCreated, map[string]any{"item": list})
}
func (s *Server) handleAddReadingListItem(w http.ResponseWriter, r *http.Request) {
	claims, err := claimsFromContext(r.Context())
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, map[string]any{"error": err.Error()})
		return
	}
	listID := r.PathValue("listID")
	var req struct {
		BookID   string `json:"book_id"`
		Position int    `json:"position"`
	}
	if err := httpx.ReadJSON(r, &req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if strings.TrimSpace(req.BookID) == "" {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": "book_id required"})
		return
	}
	list, ok, err := s.repo.GetReadingList(r.Context(), listID)
	if err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if !ok || list.UserID != claims.Sub {
		httpx.WriteJSON(w, http.StatusNotFound, map[string]any{"error": "reading list not found"})
		return
	}
	if req.Position == 0 {
		req.Position = int(time.Now().Unix() % 100000)
	}
	if err := s.repo.AddReadingListItem(r.Context(), model.ReadingListItem{ReadingListID: listID, BookID: req.BookID, Position: req.Position, CreatedAt: time.Now().Unix()}); err != nil {
		httpx.WriteJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	items, _ := s.repo.ListReadingListItems(r.Context(), listID)
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"items": items, "count": len(items)})
}
func (s *Server) handleSearchProxy(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Query string `json:"query"`
	}
	if err := httpx.ReadJSON(r, &req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	payload, err := s.rec.Search(req.Query)
	if err != nil {
		httpx.WriteJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	httpx.WriteJSON(w, http.StatusOK, payload)
}
