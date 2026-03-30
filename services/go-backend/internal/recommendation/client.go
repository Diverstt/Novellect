package recommendation

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"novellect/go-backend/internal/model"
)

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func NewClient(baseURL string) *Client {
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), HTTP: &http.Client{Timeout: 90 * time.Second}}
}

func (c *Client) post(path string, payload any) (map[string]any, error) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequest(http.MethodPost, c.BaseURL+path, bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("recommendation service returned %d", resp.StatusCode)
	}
	var result map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func (c *Client) get(path string, q url.Values) (map[string]any, error) {
	endpoint := c.BaseURL + path
	if q != nil && len(q) > 0 {
		endpoint += "?" + q.Encode()
	}
	resp, err := c.HTTP.Get(endpoint)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("recommendation service returned %d", resp.StatusCode)
	}
	var result map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func (c *Client) Health() (map[string]any, error) { return c.get("/healthz", nil) }

func (c *Client) SaveOnboarding(userID string, payload map[string]any) (map[string]any, error) {
	return c.post("/api/v1/profile/"+userID+"/onboarding", payload)
}

func (c *Client) IngestInteraction(event model.Interaction) (map[string]any, error) {
	metadata := event.Metadata
	if metadata == nil {
		metadata = map[string]any{}
	}
	return c.post("/api/v1/interactions/ingest", map[string]any{
		"user_id":    event.UserID,
		"book_id":    event.BookID,
		"action":     event.Action,
		"session_id": event.SessionID,
		"source":     event.Source,
		"metadata":   metadata,
	})
}

func (c *Client) FetchFeed(userID, mode, searchContext, recommendationContext string, limit int, sessionID, seedBookID, query string, excludeBookIDs []string, cursor string, offset *int) (map[string]any, error) {
	payload := map[string]any{
		"search_context":         searchContext,
		"user_id":                userID,
		"mode":                   mode,
		"recommendation_context": recommendationContext,
		"limit":                  limit,
		"session_id":             sessionID,
		"seed_book_id":           seedBookID,
		"query":                  query,
		"exclude_book_ids":       excludeBookIDs,
	}
	if cursor != "" {
		payload["cursor"] = cursor
	}
	if offset != nil {
		payload["offset"] = *offset
	}
	return c.post("/api/v1/recommendations/feed", payload)
}

func (c *Client) GetProfile(userID, sessionID string) (map[string]any, error) {
	q := url.Values{}
	if sessionID != "" {
		q.Set("session_id", sessionID)
	}
	return c.get("/api/v1/profile/"+userID, q)
}

func (c *Client) Search(query string) (map[string]any, error) {
	return c.post("/api/v1/search/query", map[string]any{"query": query})
}

func (c *Client) BookPreview(bookID string) (map[string]any, error) {
	return c.get("/api/v1/books/"+url.PathEscape(bookID)+"/preview", nil)
}

func (c *Client) BookContent(bookID string) (map[string]any, error) {
	return c.get("/api/v1/books/"+url.PathEscape(bookID)+"/content", nil)
}
