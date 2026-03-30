package cache

import (
	"context"
	"encoding/json"
	"time"

	"novellect/go-backend/internal/redisclient"
)

type RedisCache struct {
	client   *redisclient.Client
	cacheTTL time.Duration
	stateTTL time.Duration
}

func NewRedisCache(client *redisclient.Client, cacheTTL, stateTTL time.Duration) *RedisCache {
	return &RedisCache{client: client, cacheTTL: cacheTTL, stateTTL: stateTTL}
}
func (c *RedisCache) Ping(ctx context.Context) error { return c.client.Ping(ctx) }
func (c *RedisCache) GetJSON(ctx context.Context, key string, target any) (bool, error) {
	raw, ok, err := c.client.Get(ctx, key)
	if err != nil || !ok {
		return false, err
	}
	if err := json.Unmarshal([]byte(raw), target); err != nil {
		return false, err
	}
	return true, nil
}
func (c *RedisCache) SetJSON(ctx context.Context, key string, payload any, ttl time.Duration) error {
	raw, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	if ttl <= 0 {
		ttl = c.cacheTTL
	}
	return c.client.SetEX(ctx, key, string(raw), ttl)
}
func (c *RedisCache) Delete(ctx context.Context, keys ...string) error {
	return c.client.Del(ctx, keys...)
}
func (c *RedisCache) DeletePrefix(ctx context.Context, prefix string) error {
	keys, err := c.client.ScanPrefix(ctx, prefix)
	if err != nil {
		return err
	}
	if len(keys) == 0 {
		return nil
	}
	return c.client.Del(ctx, keys...)
}
func (c *RedisCache) CacheTTL() time.Duration { return c.cacheTTL }
func (c *RedisCache) StateTTL() time.Duration { return c.stateTTL }
