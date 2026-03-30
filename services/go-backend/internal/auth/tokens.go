package auth

import (
	"errors"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"novellect/go-backend/internal/model"
)

type TokenManager struct {
	secret []byte
	ttl    time.Duration
	issuer string
}
type Claims struct {
	Sub         string `json:"sub"`
	Email       string `json:"email"`
	DisplayName string `json:"display_name"`
	jwt.RegisteredClaims
}

func NewTokenManager(secret string, ttl time.Duration) *TokenManager {
	return &TokenManager{secret: []byte(secret), ttl: ttl, issuer: "novellect-go-backend"}
}
func (tm *TokenManager) Issue(user model.User) (string, error) {
	now := time.Now()
	claims := Claims{
		Sub:         user.ID,
		Email:       user.Email,
		DisplayName: user.DisplayName,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    tm.issuer,
			Subject:   user.ID,
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(tm.ttl)),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString(tm.secret)
}
func (tm *TokenManager) Parse(token string) (Claims, error) {
	claims := Claims{}
	parser := jwt.NewParser(
		jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}),
		jwt.WithIssuedAt(),
		jwt.WithExpirationRequired(),
	)
	parsed, err := parser.ParseWithClaims(token, &claims, func(parsed *jwt.Token) (any, error) {
		if parsed.Method != jwt.SigningMethodHS256 {
			return nil, errors.New("unexpected signing method")
		}
		return tm.secret, nil
	})
	if err != nil {
		return Claims{}, err
	}
	if !parsed.Valid {
		return Claims{}, errors.New("invalid token")
	}
	if claims.Issuer != "" && claims.Issuer != tm.issuer {
		return Claims{}, errors.New("invalid token issuer")
	}
	if claims.Sub == "" {
		claims.Sub = claims.Subject
	}
	return claims, nil
}
