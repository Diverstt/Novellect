package config

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	HTTPAddr          string
	JWTSecret         string
	AccessTTL         time.Duration
	RefreshTTL        time.Duration
	PythonBaseURL     string
	RepositoryBackend string
	LocalDataFile     string
	PGHost            string
	PGPort            int
	PGUser            string
	PGPassword        string
	PGDatabase        string
	PGTimeout         time.Duration
	RedisHost         string
	RedisPort         int
	RedisTimeout      time.Duration
	CacheTTL          time.Duration
	StateTTL          time.Duration
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
func envInt(key string, fallback int) int {
	if raw := env(key, ""); raw != "" {
		if value, err := strconv.Atoi(raw); err == nil {
			return value
		}
	}
	return fallback
}
func envDurationSeconds(key string, fallback int) time.Duration {
	return time.Duration(envInt(key, fallback)) * time.Second
}
func Load() Config {
	return Config{
		HTTPAddr: env("GO_BACKEND_HTTP_ADDR", ":8080"), JWTSecret: env("GO_BACKEND_JWT_SECRET", "novellect-dev-secret"), AccessTTL: envDurationSeconds("GO_BACKEND_ACCESS_TTL_SEC", 900), RefreshTTL: envDurationSeconds("GO_BACKEND_REFRESH_TTL_SEC", 1209600), PythonBaseURL: env("PYTHON_RECOMMENDATION_URL", "http://127.0.0.1:8000"), RepositoryBackend: strings.ToLower(env("GO_BACKEND_REPOSITORY_BACKEND", "auto")), LocalDataFile: env("GO_BACKEND_LOCAL_DATA_FILE", "data/local_store.json"), PGHost: env("POSTGRES_HOST", "127.0.0.1"), PGPort: envInt("POSTGRES_PORT", 5432), PGUser: env("POSTGRES_USER", "novellect"), PGPassword: env("POSTGRES_PASSWORD", "novellect"), PGDatabase: env("POSTGRES_DB", "novellect"), PGTimeout: envDurationSeconds("POSTGRES_TIMEOUT_SEC", 5), RedisHost: env("REDIS_HOST", "127.0.0.1"), RedisPort: envInt("REDIS_PORT", 6379), RedisTimeout: envDurationSeconds("REDIS_TIMEOUT_SEC", 3), CacheTTL: envDurationSeconds("REDIS_CACHE_TTL_SEC", 300), StateTTL: envDurationSeconds("REDIS_STATE_TTL_SEC", 7200),
	}
}

func (c Config) Validate() error {
	if strings.TrimSpace(c.HTTPAddr) == "" {
		return errors.New("GO_BACKEND_HTTP_ADDR must not be empty")
	}
	if len(strings.TrimSpace(c.JWTSecret)) < 8 {
		return errors.New("GO_BACKEND_JWT_SECRET must be at least 8 characters")
	}
	if c.AccessTTL <= 0 || c.RefreshTTL <= 0 {
		return errors.New("access and refresh TTL must be positive")
	}
	if c.RefreshTTL <= c.AccessTTL {
		return errors.New("refresh TTL must be greater than access TTL")
	}
	if _, err := url.ParseRequestURI(c.PythonBaseURL); err != nil {
		return fmt.Errorf("invalid PYTHON_RECOMMENDATION_URL: %w", err)
	}
	if c.RepositoryBackend != "" && c.RepositoryBackend != "auto" && c.RepositoryBackend != "postgres" && c.RepositoryBackend != "local" {
		return errors.New("GO_BACKEND_REPOSITORY_BACKEND must be auto, postgres, or local")
	}
	if strings.TrimSpace(c.LocalDataFile) == "" {
		return errors.New("GO_BACKEND_LOCAL_DATA_FILE must not be empty")
	}
	if strings.TrimSpace(c.PGHost) == "" || strings.TrimSpace(c.PGUser) == "" || strings.TrimSpace(c.PGDatabase) == "" {
		return errors.New("postgres host/user/database must not be empty")
	}
	if c.PGPort <= 0 || c.PGPort > 65535 || c.RedisPort <= 0 || c.RedisPort > 65535 {
		return errors.New("postgres/redis ports must be between 1 and 65535")
	}
	if c.PGTimeout <= 0 || c.RedisTimeout <= 0 || c.CacheTTL <= 0 || c.StateTTL <= 0 {
		return errors.New("timeouts and cache TTLs must be positive")
	}
	return nil
}
