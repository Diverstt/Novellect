package auth

import "testing"

func TestHashPasswordUsesBcrypt(t *testing.T) {
	hashed, err := HashPassword("secret12")
	if err != nil {
		t.Fatal(err)
	}
	if hashed == "" || hashed[:3] != "$2a" && hashed[:3] != "$2b" && hashed[:3] != "$2y" {
		t.Fatalf("expected bcrypt hash, got %q", hashed)
	}
	if !VerifyPassword(hashed, "secret12") {
		t.Fatalf("bcrypt hash should verify")
	}
	if NeedsPasswordRehash(hashed) {
		t.Fatalf("bcrypt hash should not need rehash")
	}
}

func TestVerifyPasswordSupportsLegacyHash(t *testing.T) {
	legacy, err := func() (string, error) {
		salt, err := randomString(18)
		if err != nil {
			return "", err
		}
		return salt + "$" + derive("legacy-secret", salt, 12000), nil
	}()
	if err != nil {
		t.Fatal(err)
	}
	if !VerifyPassword(legacy, "legacy-secret") {
		t.Fatalf("legacy hash should still verify during migration")
	}
	if !NeedsPasswordRehash(legacy) {
		t.Fatalf("legacy hash should require rehash")
	}
}
