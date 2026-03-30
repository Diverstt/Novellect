CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_accounts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_subject TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(provider, provider_subject)
);
CREATE INDEX IF NOT EXISTS idx_auth_accounts_user_id ON auth_accounts(user_id);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT NOT NULL DEFAULT '',
    remote_addr TEXT NOT NULL DEFAULT '',
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    revoked_at BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at ON refresh_tokens(expires_at);

CREATE TABLE IF NOT EXISTS onboarding_state (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    onboarding_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_recomputed_at BIGINT NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_profiles_updated_at ON user_profiles(updated_at DESC);

CREATE TABLE IF NOT EXISTS user_interactions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL,
    action TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'api',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_interactions_user_created ON user_interactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_interactions_book_id ON user_interactions(book_id);
CREATE INDEX IF NOT EXISTS idx_user_interactions_session ON user_interactions(user_id, session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reading_lists (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'custom',
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reading_lists_user_created ON reading_lists(user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reading_lists_saved_kind ON reading_lists(user_id, kind) WHERE kind = 'saved';

CREATE TABLE IF NOT EXISTS reading_list_items (
    reading_list_id TEXT NOT NULL REFERENCES reading_lists(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    PRIMARY KEY (reading_list_id, book_id)
);
CREATE INDEX IF NOT EXISTS idx_reading_list_items_position ON reading_list_items(reading_list_id, position, created_at);

CREATE TABLE IF NOT EXISTS recommendation_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    seed_book_id TEXT NOT NULL DEFAULT '',
    request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recommendation_events_user_created ON recommendation_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendation_events_session ON recommendation_events(user_id, session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS session_state (
    session_key TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_state_user_updated ON session_state(user_id, updated_at DESC);
