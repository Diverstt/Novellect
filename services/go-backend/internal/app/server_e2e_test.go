package app

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"novellect/go-backend/internal/cache"
	"novellect/go-backend/internal/config"
	"novellect/go-backend/internal/pgclient"
	"novellect/go-backend/internal/redisclient"
	"novellect/go-backend/internal/repository"
)

type fakeRedis struct {
	ln       net.Listener
	mu       sync.Mutex
	store    map[string]string
	expires  map[string]time.Time
	delCalls int
}

func newFakeRedis(t *testing.T) *fakeRedis {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	fr := &fakeRedis{ln: ln, store: map[string]string{}, expires: map[string]time.Time{}}
	go fr.serve(t)
	return fr
}

func (fr *fakeRedis) addr() (string, int) {
	host, portText, _ := net.SplitHostPort(fr.ln.Addr().String())
	port, _ := strconv.Atoi(portText)
	return host, port
}

func (fr *fakeRedis) close() { _ = fr.ln.Close() }

func (fr *fakeRedis) purgeExpired() {
	now := time.Now()
	for key, exp := range fr.expires {
		if !exp.IsZero() && exp.Before(now) {
			delete(fr.expires, key)
			delete(fr.store, key)
		}
	}
}

func (fr *fakeRedis) serve(t *testing.T) {
	for {
		conn, err := fr.ln.Accept()
		if err != nil {
			return
		}
		go fr.handleConn(t, conn)
	}
}

func (fr *fakeRedis) handleConn(t *testing.T, conn net.Conn) {
	defer conn.Close()
	rw := bufio.NewReadWriter(bufio.NewReader(conn), bufio.NewWriter(conn))
	for {
		args, err := readRESPArray(rw.Reader)
		if err != nil {
			return
		}
		if len(args) == 0 {
			continue
		}
		cmd := strings.ToUpper(args[0])
		fr.mu.Lock()
		fr.purgeExpired()
		switch cmd {
		case "PING":
			writeSimpleString(rw.Writer, "PONG")
		case "GET":
			value, ok := fr.store[args[1]]
			if !ok {
				writeBulkNil(rw.Writer)
			} else {
				writeBulkString(rw.Writer, value)
			}
		case "SETEX":
			ttlSec, _ := strconv.Atoi(args[2])
			fr.store[args[1]] = args[3]
			fr.expires[args[1]] = time.Now().Add(time.Duration(ttlSec) * time.Second)
			writeSimpleString(rw.Writer, "OK")
		case "DEL":
			deleted := int64(0)
			for _, key := range args[1:] {
				if _, ok := fr.store[key]; ok {
					deleted++
					delete(fr.store, key)
					delete(fr.expires, key)
				}
			}
			fr.delCalls++
			writeInteger(rw.Writer, deleted)
		case "SCAN":
			pattern := strings.TrimSuffix(args[3], "*")
			keys := []string{}
			for key := range fr.store {
				if strings.HasPrefix(key, pattern) {
					keys = append(keys, key)
				}
			}
			sort.Strings(keys)
			writeArray(rw.Writer, []any{"0", keys})
		default:
			writeError(rw.Writer, "ERR unsupported command")
		}
		fr.mu.Unlock()
		_ = rw.Flush()
	}
}

func readRESPArray(r *bufio.Reader) ([]string, error) {
	prefix, err := r.ReadByte()
	if err != nil {
		return nil, err
	}
	if prefix != '*' {
		return nil, fmt.Errorf("expected array")
	}
	line, err := readLine(r)
	if err != nil {
		return nil, err
	}
	count, _ := strconv.Atoi(line)
	values := make([]string, 0, count)
	for i := 0; i < count; i++ {
		if prefix, err = r.ReadByte(); err != nil || prefix != '$' {
			return nil, fmt.Errorf("expected bulk")
		}
		rawLen, err := readLine(r)
		if err != nil {
			return nil, err
		}
		length, _ := strconv.Atoi(rawLen)
		data := make([]byte, length+2)
		if _, err := io.ReadFull(r, data); err != nil {
			return nil, err
		}
		values = append(values, string(data[:length]))
	}
	return values, nil
}

func writeSimpleString(w io.Writer, value string) { fmt.Fprintf(w, "+%s\r\n", value) }
func writeError(w io.Writer, value string)        { fmt.Fprintf(w, "-%s\r\n", value) }
func writeInteger(w io.Writer, value int64)       { fmt.Fprintf(w, ":%d\r\n", value) }
func writeBulkString(w io.Writer, value string)   { fmt.Fprintf(w, "$%d\r\n%s\r\n", len(value), value) }
func writeBulkNil(w io.Writer)                    { io.WriteString(w, "$-1\r\n") }
func writeArray(w io.Writer, items []any) {
	fmt.Fprintf(w, "*%d\r\n", len(items))
	for _, item := range items {
		switch v := item.(type) {
		case string:
			writeBulkString(w, v)
		case []string:
			fmt.Fprintf(w, "*%d\r\n", len(v))
			for _, inner := range v {
				writeBulkString(w, inner)
			}
		}
	}
}

type fakePostgres struct {
	ln           net.Listener
	mu           sync.Mutex
	users        map[string][]string
	usersByEmail map[string]string
	refresh      map[string][]string
	onboarding   map[string][]string
	profiles     map[string][]string
	interactions [][]string
	lists        map[string][]string
	listItems    [][]string
	recEvents    [][]string
	sessions     map[string][]string
}

func newFakePostgres(t *testing.T) *fakePostgres {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	fp := &fakePostgres{ln: ln, users: map[string][]string{}, usersByEmail: map[string]string{}, refresh: map[string][]string{}, onboarding: map[string][]string{}, profiles: map[string][]string{}, lists: map[string][]string{}, sessions: map[string][]string{}}
	go fp.serve(t)
	return fp
}

func (fp *fakePostgres) addr() (string, int) {
	host, portText, _ := net.SplitHostPort(fp.ln.Addr().String())
	port, _ := strconv.Atoi(portText)
	return host, port
}
func (fp *fakePostgres) close() { _ = fp.ln.Close() }

func (fp *fakePostgres) serve(t *testing.T) {
	for {
		conn, err := fp.ln.Accept()
		if err != nil {
			return
		}
		go fp.handleConn(t, conn)
	}
}

func (fp *fakePostgres) handleConn(t *testing.T, conn net.Conn) {
	defer conn.Close()
	rw := bufio.NewReadWriter(bufio.NewReader(conn), bufio.NewWriter(conn))
	if err := handleStartup(rw); err != nil {
		return
	}
	for {
		typ, payload, err := readPGMessage(rw.Reader)
		if err != nil {
			return
		}
		switch typ {
		case 'Q':
			query := strings.TrimRight(string(payload), "\x00")
			fp.handleQuery(rw.Writer, query)
			_ = rw.Flush()
		case 'X':
			return
		}
	}
}

func handleStartup(rw *bufio.ReadWriter) error {
	lengthBytes := make([]byte, 4)
	if _, err := io.ReadFull(rw.Reader, lengthBytes); err != nil {
		return err
	}
	length := int(binary.BigEndian.Uint32(lengthBytes))
	payload := make([]byte, length-4)
	if _, err := io.ReadFull(rw.Reader, payload); err != nil {
		return err
	}
	// ask for md5 password
	writePGAuthMD5(rw.Writer, []byte{1, 2, 3, 4})
	_ = rw.Flush()
	typ, _, err := readPGMessage(rw.Reader)
	if err != nil || typ != 'p' {
		return fmt.Errorf("expected password")
	}
	writePGAuthOK(rw.Writer)
	writePGReady(rw.Writer)
	return rw.Flush()
}

func readPGMessage(r *bufio.Reader) (byte, []byte, error) {
	typ, err := r.ReadByte()
	if err != nil {
		return 0, nil, err
	}
	lengthBytes := make([]byte, 4)
	if _, err := io.ReadFull(r, lengthBytes); err != nil {
		return 0, nil, err
	}
	length := int(binary.BigEndian.Uint32(lengthBytes))
	payload := make([]byte, length-4)
	if _, err := io.ReadFull(r, payload); err != nil {
		return 0, nil, err
	}
	return typ, payload, nil
}

func writePGMessage(w io.Writer, typ byte, payload []byte) {
	_ = binary.Write(w, binary.BigEndian, typ)
	_ = binary.Write(w, binary.BigEndian, int32(len(payload)+4))
	_, _ = w.Write(payload)
}
func writePGAuthMD5(w io.Writer, salt []byte) {
	var buf bytes.Buffer
	_ = binary.Write(&buf, binary.BigEndian, int32(5))
	buf.Write(salt)
	writePGMessage(w, 'R', buf.Bytes())
}
func writePGAuthOK(w io.Writer) {
	var buf bytes.Buffer
	_ = binary.Write(&buf, binary.BigEndian, int32(0))
	writePGMessage(w, 'R', buf.Bytes())
}
func writePGReady(w io.Writer)               { writePGMessage(w, 'Z', []byte{'I'}) }
func writePGCommand(w io.Writer, tag string) { writePGMessage(w, 'C', append([]byte(tag), 0)) }
func writePGError(w io.Writer, message string) {
	var buf bytes.Buffer
	buf.WriteByte('M')
	buf.WriteString(message)
	buf.WriteByte(0)
	buf.WriteByte(0)
	writePGMessage(w, 'E', buf.Bytes())
	writePGReady(w)
}

func writePGRows(w io.Writer, columns []string, rows [][]string) {
	var desc bytes.Buffer
	_ = binary.Write(&desc, binary.BigEndian, int16(len(columns)))
	for _, col := range columns {
		desc.WriteString(col)
		desc.WriteByte(0)
		_ = binary.Write(&desc, binary.BigEndian, int32(0))
		_ = binary.Write(&desc, binary.BigEndian, int16(0))
		_ = binary.Write(&desc, binary.BigEndian, int32(25))
		_ = binary.Write(&desc, binary.BigEndian, int16(-1))
		_ = binary.Write(&desc, binary.BigEndian, int32(-1))
		_ = binary.Write(&desc, binary.BigEndian, int16(0))
	}
	writePGMessage(w, 'T', desc.Bytes())
	for _, row := range rows {
		var data bytes.Buffer
		_ = binary.Write(&data, binary.BigEndian, int16(len(row)))
		for _, value := range row {
			_ = binary.Write(&data, binary.BigEndian, int32(len(value)))
			data.WriteString(value)
		}
		writePGMessage(w, 'D', data.Bytes())
	}
	writePGCommand(w, fmt.Sprintf("SELECT %d", len(rows)))
	writePGReady(w)
}

func splitCSVSQL(input string) []string {
	items := []string{}
	var current strings.Builder
	inQuote := false
	for i := 0; i < len(input); i++ {
		ch := input[i]
		if ch == '\'' {
			current.WriteByte(ch)
			if inQuote && i+1 < len(input) && input[i+1] == '\'' {
				current.WriteByte(input[i+1])
				i++
				continue
			}
			inQuote = !inQuote
			continue
		}
		if ch == ',' && !inQuote {
			items = append(items, strings.TrimSpace(current.String()))
			current.Reset()
			continue
		}
		current.WriteByte(ch)
	}
	if current.Len() > 0 {
		items = append(items, strings.TrimSpace(current.String()))
	}
	return items
}

func decodeSQLValue(raw string) string {
	value := strings.TrimSpace(raw)
	value = strings.TrimSuffix(value, "::jsonb")
	if strings.HasPrefix(value, "'") && strings.HasSuffix(value, "'") {
		value = strings.TrimSuffix(strings.TrimPrefix(value, "'"), "'")
		value = strings.ReplaceAll(value, "''", "'")
	}
	return value
}

func valuesClause(query string) []string {
	upper := strings.ToUpper(query)
	start := strings.Index(upper, "VALUES (")
	if start < 0 {
		return nil
	}
	rest := query[start+8:]
	end := strings.Index(rest, ")")
	return splitCSVSQL(rest[:end])
}

func whereLiteral(query, field string) string {
	marker := field + "='"
	idx := strings.Index(query, marker)
	if idx < 0 {
		return ""
	}
	rest := query[idx+len(marker):]
	end := strings.Index(rest, "'")
	return strings.ReplaceAll(rest[:end], "''", "'")
}

func (fp *fakePostgres) handleQuery(w io.Writer, query string) {
	fp.mu.Lock()
	defer fp.mu.Unlock()
	upper := strings.ToUpper(query)
	switch {
	case strings.HasPrefix(upper, "SELECT 1"):
		writePGRows(w, []string{"?column?"}, [][]string{{"1"}})
	case strings.HasPrefix(upper, "INSERT INTO USERS"):
		v := valuesClause(query)
		row := []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3]), decodeSQLValue(v[4]), decodeSQLValue(v[5])}
		fp.users[row[0]] = row
		fp.usersByEmail[row[1]] = row[0]
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.HasPrefix(upper, "INSERT INTO AUTH_ACCOUNTS"):
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM USERS WHERE EMAIL="):
		email := whereLiteral(query, "email")
		if id, ok := fp.usersByEmail[email]; ok {
			writePGRows(w, []string{"id", "email", "display_name", "password_hash", "created_at", "updated_at"}, [][]string{fp.users[id]})
		} else {
			writePGRows(w, []string{"id", "email", "display_name", "password_hash", "created_at", "updated_at"}, [][]string{})
		}
	case strings.Contains(upper, "FROM USERS WHERE ID="):
		id := whereLiteral(query, "id")
		if row, ok := fp.users[id]; ok {
			writePGRows(w, []string{"id", "email", "display_name", "password_hash", "created_at", "updated_at"}, [][]string{row})
		} else {
			writePGRows(w, []string{"id", "email", "display_name", "password_hash", "created_at", "updated_at"}, [][]string{})
		}
	case strings.HasPrefix(upper, "INSERT INTO REFRESH_TOKENS"):
		v := valuesClause(query)
		row := []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3]), decodeSQLValue(v[4]), decodeSQLValue(v[5]), decodeSQLValue(v[6]), decodeSQLValue(v[7])}
		fp.refresh[row[2]] = row
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM REFRESH_TOKENS WHERE TOKEN_HASH="):
		hash := whereLiteral(query, "token_hash")
		if row, ok := fp.refresh[hash]; ok {
			writePGRows(w, []string{"id", "user_id", "token_hash", "user_agent", "remote_addr", "created_at", "expires_at", "revoked_at"}, [][]string{row})
		} else {
			writePGRows(w, []string{"id", "user_id", "token_hash", "user_agent", "remote_addr", "created_at", "expires_at", "revoked_at"}, [][]string{})
		}
	case strings.HasPrefix(upper, "UPDATE REFRESH_TOKENS SET REVOKED_AT="):
		hash := whereLiteral(query, "token_hash")
		if row, ok := fp.refresh[hash]; ok {
			eq := strings.Index(upper, "REVOKED_AT=")
			until := strings.Index(upper[eq:], " WHERE")
			row[7] = strings.TrimSpace(query[eq+11 : eq+until])
			fp.refresh[hash] = row
		}
		writePGCommand(w, "UPDATE 1")
		writePGReady(w)
	case strings.HasPrefix(upper, "INSERT INTO ONBOARDING_STATE"):
		v := valuesClause(query)
		fp.onboarding[decodeSQLValue(v[0])] = []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3])}
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM ONBOARDING_STATE WHERE USER_ID="):
		userID := whereLiteral(query, "user_id")
		if row, ok := fp.onboarding[userID]; ok {
			writePGRows(w, []string{"user_id", "payload", "completed", "updated_at"}, [][]string{row})
		} else {
			writePGRows(w, []string{"user_id", "payload", "completed", "updated_at"}, [][]string{})
		}
	case strings.HasPrefix(upper, "INSERT INTO USER_PROFILES"):
		v := valuesClause(query)
		fp.profiles[decodeSQLValue(v[0])] = []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3]), decodeSQLValue(v[4]), decodeSQLValue(v[5])}
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM USER_PROFILES WHERE USER_ID="):
		userID := whereLiteral(query, "user_id")
		if row, ok := fp.profiles[userID]; ok {
			writePGRows(w, []string{"user_id", "onboarding_json", "profile_json", "last_recomputed_at", "created_at", "updated_at"}, [][]string{row})
		} else {
			writePGRows(w, []string{"user_id", "onboarding_json", "profile_json", "last_recomputed_at", "created_at", "updated_at"}, [][]string{})
		}
	case strings.HasPrefix(upper, "INSERT INTO USER_INTERACTIONS"):
		v := valuesClause(query)
		fp.interactions = append(fp.interactions, []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3]), decodeSQLValue(v[4]), decodeSQLValue(v[5]), decodeSQLValue(v[6]), decodeSQLValue(v[7])})
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.HasPrefix(upper, "INSERT INTO READING_LISTS"):
		v := valuesClause(query)
		row := []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3]), decodeSQLValue(v[4])}
		fp.lists[row[0]] = row
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM READING_LISTS WHERE USER_ID=") && strings.Contains(upper, "AND KIND="):
		userID := whereLiteral(query, "user_id")
		kind := whereLiteral(query, "kind")
		rows := [][]string{}
		for _, row := range fp.lists {
			if row[1] == userID && row[3] == kind {
				rows = append(rows, row)
			}
		}
		writePGRows(w, []string{"id", "user_id", "name", "kind", "created_at"}, rows)
	case strings.Contains(upper, "FROM READING_LISTS WHERE ID="):
		id := whereLiteral(query, "id")
		if row, ok := fp.lists[id]; ok {
			writePGRows(w, []string{"id", "user_id", "name", "kind", "created_at"}, [][]string{row})
		} else {
			writePGRows(w, []string{"id", "user_id", "name", "kind", "created_at"}, [][]string{})
		}
	case strings.Contains(upper, "FROM READING_LISTS WHERE USER_ID="):
		userID := whereLiteral(query, "user_id")
		rows := [][]string{}
		for _, row := range fp.lists {
			if row[1] == userID {
				rows = append(rows, row)
			}
		}
		sort.Slice(rows, func(i, j int) bool { return rows[i][4] > rows[j][4] })
		writePGRows(w, []string{"id", "user_id", "name", "kind", "created_at"}, rows)
	case strings.HasPrefix(upper, "INSERT INTO READING_LIST_ITEMS"):
		v := valuesClause(query)
		row := []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3])}
		replaced := false
		for i, existing := range fp.listItems {
			if existing[0] == row[0] && existing[1] == row[1] {
				fp.listItems[i] = row
				replaced = true
			}
		}
		if !replaced {
			fp.listItems = append(fp.listItems, row)
		}
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM READING_LIST_ITEMS WHERE READING_LIST_ID="):
		id := whereLiteral(query, "reading_list_id")
		rows := [][]string{}
		for _, row := range fp.listItems {
			if row[0] == id {
				rows = append(rows, row)
			}
		}
		writePGRows(w, []string{"reading_list_id", "book_id", "position", "created_at"}, rows)
	case strings.HasPrefix(upper, "INSERT INTO RECOMMENDATION_EVENTS"):
		v := valuesClause(query)
		fp.recEvents = append(fp.recEvents, []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3]), decodeSQLValue(v[4]), decodeSQLValue(v[5]), decodeSQLValue(v[6]), decodeSQLValue(v[7])})
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM RECOMMENDATION_EVENTS WHERE USER_ID="):
		userID := whereLiteral(query, "user_id")
		rows := [][]string{}
		for i := len(fp.recEvents) - 1; i >= 0; i-- {
			if fp.recEvents[i][1] == userID {
				rows = append(rows, []string{fp.recEvents[i][6]})
			}
		}
		writePGRows(w, []string{"response_json"}, rows)
	case strings.HasPrefix(upper, "INSERT INTO SESSION_STATE"):
		v := valuesClause(query)
		fp.sessions[decodeSQLValue(v[0])] = []string{decodeSQLValue(v[0]), decodeSQLValue(v[1]), decodeSQLValue(v[2]), decodeSQLValue(v[3])}
		writePGCommand(w, "INSERT 0 1")
		writePGReady(w)
	case strings.Contains(upper, "FROM SESSION_STATE WHERE SESSION_KEY="):
		sessionKey := whereLiteral(query, "session_key")
		if row, ok := fp.sessions[sessionKey]; ok {
			writePGRows(w, []string{"session_key", "user_id", "state_json", "updated_at"}, [][]string{row})
		} else {
			writePGRows(w, []string{"session_key", "user_id", "state_json", "updated_at"}, [][]string{})
		}
	default:
		writePGError(w, "unsupported query: "+query)
	}
}

func waitForHTTP(t *testing.T, url string) {
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		resp, err := http.Get(url)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode < 500 {
				return
			}
		}
		time.Sleep(250 * time.Millisecond)
	}
	t.Fatalf("service not ready: %s", url)
}

func TestEndToEnd_PostgresRedisBackedFlow(t *testing.T) {
	t.Parallel()
	fakePG := newFakePostgres(t)
	defer fakePG.close()
	fakeRedis := newFakeRedis(t)
	defer fakeRedis.close()

	root := filepath.Clean(filepath.Join("..", "..", "..", "python-recommendation"))
	tempDir := t.TempDir()
	pythonPort := 18080
	cmd := exec.Command("python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", strconv.Itoa(pythonPort))
	cmd.Dir = root
	cmd.Env = append(os.Environ(),
		"NOVELLECT_PROFILES_FILE="+filepath.Join(tempDir, "profiles.json"),
		"NOVELLECT_INTERACTIONS_FILE="+filepath.Join(tempDir, "events.json"),
		"NOVELLECT_SESSIONS_FILE="+filepath.Join(tempDir, "sessions.json"),
		"NOVELLECT_SEARCH_MODE=lite",
		"NOVELLECT_USE_STUB_LLM=1",
		"NOVELLECT_LLM_MODEL_ID=stub",
	)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	waitForHTTP(t, fmt.Sprintf("http://127.0.0.1:%d/healthz", pythonPort))

	seedBooks := []map[string]any{
		{"title": "Тайна старого дома", "content": "Мрачная усадьба, тайна исчезновения семьи, тревожная атмосфера, расследование и секреты.", "file_id": "book_1"},
		{"title": "Светлая дорога", "content": "Путешествие, дружба, надежда, теплая атмосфера и преодоление трудностей.", "file_id": "book_2"},
		{"title": "Доктор Живаго", "content": "История любви, тяжелого выбора, философских размышлений и исторических потрясений.", "file_id": "book_3"},
		{"title": "Замок тумана", "content": "Таинственный замок, мрачный тон, тревога, скрытый конфликт и расследование древнего секрета.", "file_id": "book_4"},
	}
	for _, payload := range seedBooks {
		raw, _ := json.Marshal(payload)
		resp, err := http.Post(fmt.Sprintf("http://127.0.0.1:%d/api/v1/library/ingest/text", pythonPort), "application/json", bytes.NewReader(raw))
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode >= 300 {
			t.Fatalf("ingest failed: %d", resp.StatusCode)
		}
	}

	pgHost, pgPort := fakePG.addr()
	redisHost, redisPort := fakeRedis.addr()
	cfg := config.Config{HTTPAddr: ":0", JWTSecret: "test-secret", AccessTTL: time.Hour, RefreshTTL: 24 * time.Hour, PythonBaseURL: fmt.Sprintf("http://127.0.0.1:%d", pythonPort), PGHost: pgHost, PGPort: pgPort, PGUser: "novellect", PGPassword: "novellect", PGDatabase: "novellect", PGTimeout: 3 * time.Second, RedisHost: redisHost, RedisPort: redisPort, RedisTimeout: 3 * time.Second, CacheTTL: 5 * time.Minute, StateTTL: time.Hour}
	repo := repository.NewPostgresRepository(pgclient.New(pgclient.Config{Host: cfg.PGHost, Port: cfg.PGPort, User: cfg.PGUser, Password: cfg.PGPassword, Database: cfg.PGDatabase, Timeout: cfg.PGTimeout}))
	cacheClient := cache.NewRedisCache(redisclient.New(redisclient.Config{Host: cfg.RedisHost, Port: cfg.RedisPort, Timeout: cfg.RedisTimeout}), cfg.CacheTTL, cfg.StateTTL)
	srv := httptest.NewServer(NewServer(cfg, repo, cacheClient, nil).Handler())
	defer srv.Close()

	register := requestJSON(t, http.MethodPost, srv.URL+"/api/v1/auth/register", map[string]any{"email": "reader@example.com", "password": "secret12", "display_name": "Reader"}, "")
	access := nestedString(register, "tokens", "access_token")
	refresh := nestedString(register, "tokens", "refresh_token")
	if access == "" || refresh == "" {
		t.Fatalf("missing tokens: %#v", register)
	}

	login := requestJSON(t, http.MethodPost, srv.URL+"/api/v1/auth/login", map[string]any{"email": "reader@example.com", "password": "secret12"}, "")
	if nestedString(login, "user", "email") != "reader@example.com" {
		t.Fatalf("unexpected login payload: %#v", login)
	}

	me := requestJSON(t, http.MethodGet, srv.URL+"/api/v1/me", nil, access)
	userID := nestedString(me, "user", "id")
	if userID == "" {
		t.Fatalf("missing user id: %#v", me)
	}

	onboarding := requestJSON(t, http.MethodPost, srv.URL+"/api/v1/onboarding", map[string]any{"favorite_moods": []string{"тревожное"}, "favorite_atmosphere": []string{"таинственная", "мрачная"}, "favorite_plot": []string{"тайна"}, "favorite_books": []string{"Тайна старого дома"}}, access)
	if onboarding["taste_profile"] == nil {
		t.Fatalf("missing taste profile: %#v", onboarding)
	}

	feed := requestJSON(t, http.MethodGet, srv.URL+"/api/v1/recommendations/feed?mode=similar&limit=3&session_id=s-1", nil, access)
	items := feed["items"].([]any)
	if len(items) == 0 {
		t.Fatalf("empty feed: %#v", feed)
	}
	pagedFeed := requestJSON(t, http.MethodGet, srv.URL+"/api/v1/recommendations/feed?mode=similar&limit=2&session_id=s-1", nil, access)
	firstPageItems := pagedFeed["items"].([]any)
	if len(firstPageItems) != 2 {
		t.Fatalf("expected first page of 2 items: %#v", pagedFeed)
	}
	pageTwoCursor := nestedString(pagedFeed, "cursor")
	if pageTwoCursor == "" {
		t.Fatalf("expected pagination cursor: %#v", pagedFeed)
	}
	secondPage := requestJSON(t, http.MethodGet, srv.URL+"/api/v1/recommendations/feed?mode=similar&limit=2&session_id=s-1&cursor="+pageTwoCursor, nil, access)
	secondPageItems := secondPage["items"].([]any)
	pageOneIDs := map[string]struct{}{}
	for _, item := range firstPageItems {
		pageOneIDs[fmt.Sprint(item.(map[string]any)["book_id"])] = struct{}{}
	}
	for _, item := range secondPageItems {
		bookID := fmt.Sprint(item.(map[string]any)["book_id"])
		if _, ok := pageOneIDs[bookID]; ok {
			t.Fatalf("page 2 repeated page 1 item %s: page1=%#v page2=%#v", bookID, firstPageItems, secondPageItems)
		}
	}

	_ = requestJSON(t, http.MethodPost, srv.URL+"/api/v1/interactions", map[string]any{"book_id": "book_1", "action": "like", "session_id": "s-1"}, access)
	_ = requestJSON(t, http.MethodPost, srv.URL+"/api/v1/interactions", map[string]any{"book_id": "book_2", "action": "save", "session_id": "s-1"}, access)
	_ = requestJSON(t, http.MethodPost, srv.URL+"/api/v1/interactions", map[string]any{"book_id": "book_3", "action": "skip", "session_id": "s-1"}, access)
	_ = requestJSON(t, http.MethodPost, srv.URL+"/api/v1/interactions", map[string]any{"book_id": "book_4", "action": "dislike", "session_id": "s-1"}, access)

	updatedFeed := requestJSON(t, http.MethodGet, srv.URL+"/api/v1/recommendations/feed?mode=new&limit=3&session_id=s-1", nil, access)
	firstItem := updatedFeed["items"].([]any)[0].(map[string]any)
	if _, ok := firstItem["explanation"].(string); !ok {
		t.Fatalf("missing explanation: %#v", firstItem)
	}

	explain := requestJSON(t, http.MethodGet, srv.URL+"/api/v1/recommendations/explain?book_id="+fmt.Sprint(firstItem["book_id"])+"&session_id=s-1", nil, access)
	if explain["item"] == nil {
		t.Fatalf("missing explain payload: %#v", explain)
	}

	lists := requestJSON(t, http.MethodGet, srv.URL+"/api/v1/reading-lists", nil, access)
	if int(lists["count"].(float64)) < 1 {
		t.Fatalf("expected saved list: %#v", lists)
	}
	listID := fmt.Sprint(lists["items"].([]any)[0].(map[string]any)["id"])

	registerSecond := requestJSON(t, http.MethodPost, srv.URL+"/api/v1/auth/register", map[string]any{"email": "intruder@example.com", "password": "secret34", "display_name": "Intruder"}, "")
	secondAccess := nestedString(registerSecond, "tokens", "access_token")
	if secondAccess == "" {
		t.Fatalf("missing second access token: %#v", registerSecond)
	}
	forbidden := requestJSONStatus(t, http.MethodPost, srv.URL+"/api/v1/reading-lists/"+listID+"/items", map[string]any{"book_id": "book_3"}, secondAccess)
	if forbidden.statusCode != http.StatusNotFound {
		t.Fatalf("expected list ownership enforcement, got %d payload=%#v", forbidden.statusCode, forbidden.payload)
	}

	refreshed := requestJSON(t, http.MethodPost, srv.URL+"/api/v1/auth/refresh", map[string]any{"refresh_token": refresh}, "")
	if nestedString(refreshed, "tokens", "access_token") == "" {
		t.Fatalf("refresh failed: %#v", refreshed)
	}

	fakePG.mu.Lock()
	if len(fakePG.users) != 2 || len(fakePG.refresh) < 4 || len(fakePG.interactions) < 4 || len(fakePG.recEvents) < 1 {
		t.Fatalf("postgres state unexpected: users=%d refresh=%d interactions=%d rec=%d", len(fakePG.users), len(fakePG.refresh), len(fakePG.interactions), len(fakePG.recEvents))
	}
	fakePG.mu.Unlock()
	fakeRedis.mu.Lock()
	if fakeRedis.delCalls == 0 || len(fakeRedis.store) == 0 {
		t.Fatalf("redis state unexpected: delCalls=%d keys=%d", fakeRedis.delCalls, len(fakeRedis.store))
	}
	fakeRedis.mu.Unlock()
}

func requestJSON(t *testing.T, method, url string, payload any, bearer string) map[string]any {
	t.Helper()
	var body io.Reader
	if payload != nil {
		raw, _ := json.Marshal(payload)
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, url, body)
	if err != nil {
		t.Fatal(err)
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		t.Fatalf("request %s %s failed: %d %s", method, url, resp.StatusCode, string(raw))
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("decode response: %v raw=%s", err, string(raw))
	}
	return decoded
}

type statusPayload struct {
	statusCode int
	payload    map[string]any
	raw        string
}

func requestJSONStatus(t *testing.T, method, url string, payload any, bearer string) statusPayload {
	t.Helper()
	var body io.Reader
	if payload != nil {
		raw, _ := json.Marshal(payload)
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, url, body)
	if err != nil {
		t.Fatal(err)
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	result := statusPayload{statusCode: resp.StatusCode, raw: string(raw)}
	if len(raw) > 0 {
		var decoded map[string]any
		if json.Unmarshal(raw, &decoded) == nil {
			result.payload = decoded
		}
	}
	return result
}

func nestedString(payload map[string]any, keys ...string) string {
	var current any = payload
	for _, key := range keys {
		mapping, _ := current.(map[string]any)
		current = mapping[key]
	}
	return fmt.Sprint(current)
}

func readLine(r *bufio.Reader) (string, error) {
	line, err := r.ReadString('\n')
	if err != nil {
		return "", err
	}
	return strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r"), nil
}
