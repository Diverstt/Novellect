package auth

import (
	"testing"
	"time"

	"novellect/go-backend/internal/model"
)

func TestTokenManagerIssueAndParse(t *testing.T) {
	tm := NewTokenManager("test-secret-123456", time.Hour)
	token, err := tm.Issue(model.User{ID: "usr_1", Email: "reader@example.com", DisplayName: "Reader"})
	if err != nil {
		t.Fatal(err)
	}
	claims, err := tm.Parse(token)
	if err != nil {
		t.Fatal(err)
	}
	if claims.Sub != "usr_1" || claims.Email != "reader@example.com" || claims.DisplayName != "Reader" {
		t.Fatalf("unexpected claims: %#v", claims)
	}
}

func TestTokenManagerRejectsTampering(t *testing.T) {
	tm := NewTokenManager("test-secret-123456", time.Hour)
	token, err := tm.Issue(model.User{ID: "usr_1", Email: "reader@example.com"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := tm.Parse(token + "tampered"); err == nil {
		t.Fatalf("expected parse error for tampered token")
	}
}
