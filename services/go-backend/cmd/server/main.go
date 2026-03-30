package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"

	"novellect/go-backend/internal/app"
	"novellect/go-backend/internal/cache"
	"novellect/go-backend/internal/config"
	"novellect/go-backend/internal/pgclient"
	"novellect/go-backend/internal/redisclient"
	"novellect/go-backend/internal/repository"
)

func main() {
	cfg := config.Load()
	if err := cfg.Validate(); err != nil {
		slog.New(slog.NewJSONHandler(os.Stdout, nil)).Error("invalid config", "error", err)
		os.Exit(1)
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	pg := pgclient.New(pgclient.Config{Host: cfg.PGHost, Port: cfg.PGPort, User: cfg.PGUser, Password: cfg.PGPassword, Database: cfg.PGDatabase, Timeout: cfg.PGTimeout})
	repo, storageMode := buildRepository(cfg, pg, logger)
	redis := redisclient.New(redisclient.Config{Host: cfg.RedisHost, Port: cfg.RedisPort, Timeout: cfg.RedisTimeout})
	cacheClient := cache.NewRedisCache(redis, cfg.CacheTTL, cfg.StateTTL)
	server := app.NewServer(cfg, repo, cacheClient, slog.NewLogLogger(logger.Handler(), slog.LevelInfo))
	logger.Info("starting server", "http_addr", cfg.HTTPAddr, "python_base_url", cfg.PythonBaseURL, "repository_backend", storageMode, "pg_host", cfg.PGHost, "pg_port", cfg.PGPort, "redis_host", cfg.RedisHost, "redis_port", cfg.RedisPort)
	if err := http.ListenAndServe(cfg.HTTPAddr, server.Handler()); err != nil {
		logger.Error("server stopped", "error", err)
		os.Exit(1)
	}
}

func buildRepository(cfg config.Config, pg *pgclient.Client, logger *slog.Logger) (repository.Repository, string) {
	if cfg.RepositoryBackend == "local" {
		repo, err := repository.NewLocalRepository(cfg.LocalDataFile)
		if err != nil {
			logger.Error("failed to initialize local repository", "error", err, "path", cfg.LocalDataFile)
			os.Exit(1)
		}
		return repo, "local"
	}

	if cfg.RepositoryBackend == "postgres" {
		return repository.NewPostgresRepository(pg), "postgres"
	}

	ctx, cancel := context.WithTimeout(context.Background(), cfg.PGTimeout)
	defer cancel()
	if err := pg.Ping(ctx); err == nil {
		return repository.NewPostgresRepository(pg), "postgres"
	}

	repo, localErr := repository.NewLocalRepository(cfg.LocalDataFile)
	if localErr != nil {
		logger.Error("failed to initialize local repository fallback", "error", localErr, "path", cfg.LocalDataFile)
		os.Exit(1)
	}
	logger.Warn("postgres unavailable, using local repository fallback", "path", cfg.LocalDataFile)
	return repo, "local"
}
