package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"strings"

	"golang.org/x/crypto/bcrypt"
)

func randomString(size int) (string, error) {
	buf := make([]byte, size)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}
func derive(password, salt string, rounds int) string {
	sum := sha256.Sum256([]byte(password + ":" + salt))
	current := sum[:]
	for i := 0; i < rounds; i++ {
		next := sha256.Sum256(append(current, []byte(salt)...))
		current = next[:]
	}
	return hex.EncodeToString(current)
}
func HashPassword(password string) (string, error) {
	hashed, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return "", err
	}
	return string(hashed), nil
}
func isLegacyPasswordHash(stored string) bool {
	parts := strings.SplitN(stored, "$", 2)
	return len(parts) == 2 && parts[0] != "" && len(parts[1]) == 64
}
func NeedsPasswordRehash(stored string) bool {
	return stored == "" || !strings.HasPrefix(stored, "$2")
}
func VerifyPassword(stored, password string) bool {
	if strings.HasPrefix(stored, "$2") {
		return bcrypt.CompareHashAndPassword([]byte(stored), []byte(password)) == nil
	}
	if isLegacyPasswordHash(stored) {
		parts := strings.SplitN(stored, "$", 2)
		candidate := derive(password, parts[0], 12000)
		return subtle.ConstantTimeCompare([]byte(parts[1]), []byte(candidate)) == 1
	}
	return false
}
func HashOpaqueToken(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])
}
func NewOpaqueToken() (string, string, error) {
	raw, err := randomString(32)
	if err != nil {
		return "", "", err
	}
	return raw, HashOpaqueToken(raw), nil
}
