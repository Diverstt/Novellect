#!/usr/bin/env bash
set -euo pipefail

BASE_GO_URL="${BASE_GO_URL:-http://localhost:8080}"
BASE_PY_URL="${BASE_PY_URL:-http://localhost:8000}"
EMAIL="${SMOKE_EMAIL:-reader@example.com}"
PASSWORD="${SMOKE_PASSWORD:-secret12}"
SESSION_ID="${SMOKE_SESSION_ID:-session-1}"

extract_json() {
  python - "$1" "$2" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
path = sys.argv[2].split('.')
cur = payload
for key in path:
    if isinstance(cur, list):
        cur = cur[int(key)]
    else:
        cur = cur[key]
print(cur)
PY
}

echo "[1/8] Seeding demo books into Python recommendation service"
curl -s -X POST "$BASE_PY_URL/api/v1/library/ingest/text" -H 'Content-Type: application/json' -d '{"title":"Тайна старого дома","content":"Мрачная усадьба, тайна исчезновения семьи, тревожная атмосфера, расследование и секреты.","file_id":"book_1"}' >/dev/null
curl -s -X POST "$BASE_PY_URL/api/v1/library/ingest/text" -H 'Content-Type: application/json' -d '{"title":"Светлая дорога","content":"Путешествие, дружба, надежда, теплая атмосфера и преодоление трудностей.","file_id":"book_2"}' >/dev/null
curl -s -X POST "$BASE_PY_URL/api/v1/library/ingest/text" -H 'Content-Type: application/json' -d '{"title":"Замок тумана","content":"Таинственный замок, мрачный тон, тревога, скрытый конфликт и расследование древнего секрета.","file_id":"book_3"}' >/dev/null

echo "[2/8] Register"
REGISTER=$(curl -s -X POST "$BASE_GO_URL/api/v1/auth/register" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"Reader\"}")
ACCESS=$(extract_json "$REGISTER" 'tokens.access_token')
REFRESH=$(extract_json "$REGISTER" 'tokens.refresh_token')

echo "[3/8] Current user"
curl -s "$BASE_GO_URL/api/v1/me" -H "Authorization: Bearer $ACCESS"

echo

echo "[4/8] Onboarding"
curl -s -X POST "$BASE_GO_URL/api/v1/onboarding" -H "Authorization: Bearer $ACCESS" -H 'Content-Type: application/json' -d '{"favorite_moods":["тревожное"],"favorite_atmosphere":["таинственная","мрачная"],"favorite_plot":["тайна"],"favorite_books":["Тайна старого дома"]}'

echo

echo "[5/8] Feed"
FEED=$(curl -s "$BASE_GO_URL/api/v1/recommendations/feed?mode=similar&limit=5&session_id=$SESSION_ID" -H "Authorization: Bearer $ACCESS")
echo "$FEED"
BOOK_ID=$(extract_json "$FEED" 'items.0.book_id')

echo

echo "[6/8] Like first recommendation"
curl -s -X POST "$BASE_GO_URL/api/v1/interactions" -H "Authorization: Bearer $ACCESS" -H 'Content-Type: application/json' -d "{\"book_id\":\"$BOOK_ID\",\"action\":\"like\",\"session_id\":\"$SESSION_ID\",\"source\":\"swipe\"}"

echo

echo "[7/8] Updated feed"
curl -s "$BASE_GO_URL/api/v1/recommendations/feed?mode=similar&limit=5&session_id=$SESSION_ID" -H "Authorization: Bearer $ACCESS"

echo

echo "[8/8] Explain recommendation + refresh"
curl -s "$BASE_GO_URL/api/v1/recommendations/explain?book_id=$BOOK_ID&session_id=$SESSION_ID" -H "Authorization: Bearer $ACCESS"
echo
curl -s -X POST "$BASE_GO_URL/api/v1/auth/refresh" -H 'Content-Type: application/json' -d "{\"refresh_token\":\"$REFRESH\"}"
