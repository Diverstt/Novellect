package pgclient

import (
	"bufio"
	"bytes"
	"crypto/hmac"
	"context"
	"crypto/md5"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"

	"golang.org/x/crypto/pbkdf2"
)

type Config struct {
	Host     string
	Port     int
	User     string
	Password string
	Database string
	Timeout  time.Duration
}

type Client struct {
	cfg Config
}

type Result struct {
	Columns    []string
	Rows       [][]string
	CommandTag string
}

type JSONBArg struct {
	Value any
}

func New(cfg Config) *Client   { return &Client{cfg: cfg} }
func JSONB(value any) JSONBArg { return JSONBArg{Value: value} }

func (c *Client) address() string { return net.JoinHostPort(c.cfg.Host, strconv.Itoa(c.cfg.Port)) }

func (c *Client) withConn(ctx context.Context, fn func(*pgConn) error) error {
	dialer := net.Dialer{Timeout: c.cfg.Timeout}
	conn, err := dialer.DialContext(ctx, "tcp", c.address())
	if err != nil {
		return err
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(deadline)
	} else {
		_ = conn.SetDeadline(time.Now().Add(c.cfg.Timeout))
	}
	pg := &pgConn{conn: conn, rw: bufio.NewReadWriter(bufio.NewReader(conn), bufio.NewWriter(conn))}
	if err := pg.startup(c.cfg); err != nil {
		return err
	}
	defer pg.terminate()
	return fn(pg)
}

func (c *Client) Ping(ctx context.Context) error {
	_, err := c.Query(ctx, "SELECT 1;")
	return err
}

func (c *Client) Exec(ctx context.Context, query string) error {
	_, err := c.Query(ctx, query)
	return err
}

func (c *Client) ExecParams(ctx context.Context, query string, args ...any) error {
	formatted, err := formatQuery(query, args...)
	if err != nil {
		return err
	}
	return c.Exec(ctx, formatted)
}

func (c *Client) Query(ctx context.Context, query string) (Result, error) {
	var result Result
	err := c.withConn(ctx, func(pg *pgConn) error {
		res, err := pg.simpleQuery(query)
		if err != nil {
			return err
		}
		result = res
		return nil
	})
	return result, err
}

func (c *Client) QueryParams(ctx context.Context, query string, args ...any) (Result, error) {
	formatted, err := formatQuery(query, args...)
	if err != nil {
		return Result{}, err
	}
	return c.Query(ctx, formatted)
}

type pgConn struct {
	conn net.Conn
	rw   *bufio.ReadWriter
}

func writeInt32(w io.Writer, value int32) error { return binary.Write(w, binary.BigEndian, value) }

func readInt32(r io.Reader) (int32, error) {
	var value int32
	err := binary.Read(r, binary.BigEndian, &value)
	return value, err
}

func md5Password(user, password string, salt []byte) string {
	first := md5.Sum([]byte(password + user))
	firstHex := hex.EncodeToString(first[:])
	second := md5.Sum(append([]byte(firstHex), salt...))
	return "md5" + hex.EncodeToString(second[:])
}

func (pg *pgConn) startup(cfg Config) error {
	var body bytes.Buffer
	_ = writeInt32(&body, 196608)
	for _, kv := range [][2]string{{"user", cfg.User}, {"database", cfg.Database}, {"client_encoding", "UTF8"}} {
		body.WriteString(kv[0])
		body.WriteByte(0)
		body.WriteString(kv[1])
		body.WriteByte(0)
	}
	body.WriteByte(0)

	var packet bytes.Buffer
	_ = writeInt32(&packet, int32(body.Len()+4))
	packet.Write(body.Bytes())
	if _, err := pg.rw.Write(packet.Bytes()); err != nil {
		return err
	}
	if err := pg.rw.Flush(); err != nil {
		return err
	}

	for {
		msgType, payload, err := pg.readMessage()
		if err != nil {
			return err
		}
		switch msgType {
		case 'R':
			if len(payload) < 4 {
				return fmt.Errorf("invalid authentication payload")
			}
			authCode := int(binary.BigEndian.Uint32(payload[:4]))
			switch authCode {
			case 0:
			case 3:
				if err := pg.password(cfg.Password); err != nil {
					return err
				}
			case 5:
				if len(payload) < 8 {
					return fmt.Errorf("invalid md5 auth payload")
				}
				if err := pg.password(md5Password(cfg.User, cfg.Password, payload[4:8])); err != nil {
					return err
				}
			case 10:
				if err := pg.scramSHA256(cfg, payload[4:]); err != nil {
					return err
				}
			default:
				return fmt.Errorf("unsupported authentication method %d", authCode)
			}
		case 'S', 'K':
		case 'Z':
			return nil
		case 'E':
			return decodeError(payload)
		}
	}
}

func (pg *pgConn) scramSHA256(cfg Config, payload []byte) error {
	mechanisms := parseCStringList(payload)
	supported := false
	for _, mechanism := range mechanisms {
		if mechanism == "SCRAM-SHA-256" {
			supported = true
			break
		}
	}
	if !supported {
		return fmt.Errorf("postgres offered unsupported SASL mechanisms: %v", mechanisms)
	}

	clientNonce, err := randomNonce()
	if err != nil {
		return err
	}
	clientFirstBare := "n=" + saslName(cfg.User) + ",r=" + clientNonce
	initialResponse := "n,," + clientFirstBare
	if err := pg.saslInitialResponse("SCRAM-SHA-256", []byte(initialResponse)); err != nil {
		return err
	}

	msgType, serverPayload, err := pg.readMessage()
	if err != nil {
		return err
	}
	if msgType == 'E' {
		return decodeError(serverPayload)
	}
	if msgType != 'R' || len(serverPayload) < 4 || int(binary.BigEndian.Uint32(serverPayload[:4])) != 11 {
		return fmt.Errorf("expected AuthenticationSASLContinue, got %q", msgType)
	}

	serverFirst := string(serverPayload[4:])
	serverAttrs := parseSCRAMAttributes(serverFirst)
	serverNonce := serverAttrs["r"]
	if !strings.HasPrefix(serverNonce, clientNonce) {
		return errors.New("invalid SCRAM nonce from postgres")
	}

	salt, err := base64.StdEncoding.DecodeString(serverAttrs["s"])
	if err != nil {
		return fmt.Errorf("invalid SCRAM salt: %w", err)
	}
	iterations, err := strconv.Atoi(serverAttrs["i"])
	if err != nil || iterations <= 0 {
		return fmt.Errorf("invalid SCRAM iteration count: %q", serverAttrs["i"])
	}

	clientFinalWithoutProof := "c=biws,r=" + serverNonce
	authMessage := clientFirstBare + "," + serverFirst + "," + clientFinalWithoutProof
	saltedPassword := pbkdf2.Key([]byte(cfg.Password), salt, iterations, sha256.Size, sha256.New)
	clientKey := hmacSHA256(saltedPassword, []byte("Client Key"))
	storedKey := sha256.Sum256(clientKey)
	clientSignature := hmacSHA256(storedKey[:], []byte(authMessage))
	clientProof := xorBytes(clientKey, clientSignature)
	serverKey := hmacSHA256(saltedPassword, []byte("Server Key"))
	expectedServerSignature := hmacSHA256(serverKey, []byte(authMessage))
	clientFinal := clientFinalWithoutProof + ",p=" + base64.StdEncoding.EncodeToString(clientProof)
	if err := pg.saslResponse([]byte(clientFinal)); err != nil {
		return err
	}

	msgType, serverPayload, err = pg.readMessage()
	if err != nil {
		return err
	}
	if msgType == 'E' {
		return decodeError(serverPayload)
	}
	if msgType != 'R' || len(serverPayload) < 4 || int(binary.BigEndian.Uint32(serverPayload[:4])) != 12 {
		return fmt.Errorf("expected AuthenticationSASLFinal, got %q", msgType)
	}

	serverFinal := string(serverPayload[4:])
	serverFinalAttrs := parseSCRAMAttributes(serverFinal)
	if serverError := serverFinalAttrs["e"]; serverError != "" {
		return fmt.Errorf("postgres SCRAM error: %s", serverError)
	}
	serverSignature, err := base64.StdEncoding.DecodeString(serverFinalAttrs["v"])
	if err != nil {
		return fmt.Errorf("invalid SCRAM server signature: %w", err)
	}
	if !hmac.Equal(serverSignature, expectedServerSignature) {
		return errors.New("invalid SCRAM server signature")
	}

	return nil
}

func (pg *pgConn) saslInitialResponse(mechanism string, response []byte) error {
	var payload bytes.Buffer
	payload.WriteString(mechanism)
	payload.WriteByte(0)
	if err := writeInt32(&payload, int32(len(response))); err != nil {
		return err
	}
	payload.Write(response)
	return pg.writeMessage('p', payload.Bytes())
}

func (pg *pgConn) saslResponse(response []byte) error {
	return pg.writeMessage('p', response)
}

func parseCStringList(payload []byte) []string {
	parts := bytes.Split(payload, []byte{0})
	items := make([]string, 0, len(parts))
	for _, part := range parts {
		if len(part) == 0 {
			continue
		}
		items = append(items, string(part))
	}
	return items
}

func parseSCRAMAttributes(message string) map[string]string {
	attrs := make(map[string]string)
	for _, part := range strings.Split(message, ",") {
		if len(part) < 3 || part[1] != '=' {
			continue
		}
		attrs[part[:1]] = part[2:]
	}
	return attrs
}

func randomNonce() (string, error) {
	raw := make([]byte, 18)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return base64.RawStdEncoding.EncodeToString(raw), nil
}

func saslName(value string) string {
	replacer := strings.NewReplacer("=", "=3D", ",", "=2C")
	return replacer.Replace(value)
}

func hmacSHA256(key, payload []byte) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write(payload)
	return mac.Sum(nil)
}

func xorBytes(left, right []byte) []byte {
	size := len(left)
	if len(right) < size {
		size = len(right)
	}
	result := make([]byte, size)
	for idx := 0; idx < size; idx++ {
		result[idx] = left[idx] ^ right[idx]
	}
	return result
}

func (pg *pgConn) password(value string) error {
	var payload bytes.Buffer
	payload.WriteString(value)
	payload.WriteByte(0)
	return pg.writeMessage('p', payload.Bytes())
}

func (pg *pgConn) writeMessage(msgType byte, payload []byte) error {
	if err := pg.rw.WriteByte(msgType); err != nil {
		return err
	}
	if err := writeInt32(pg.rw, int32(len(payload)+4)); err != nil {
		return err
	}
	if _, err := pg.rw.Write(payload); err != nil {
		return err
	}
	return pg.rw.Flush()
}

func (pg *pgConn) readMessage() (byte, []byte, error) {
	msgType, err := pg.rw.ReadByte()
	if err != nil {
		return 0, nil, err
	}
	length, err := readInt32(pg.rw)
	if err != nil {
		return 0, nil, err
	}
	if length < 4 {
		return 0, nil, fmt.Errorf("invalid message length %d", length)
	}
	payload := make([]byte, length-4)
	if _, err := io.ReadFull(pg.rw, payload); err != nil {
		return 0, nil, err
	}
	return msgType, payload, nil
}

func (pg *pgConn) simpleQuery(query string) (Result, error) {
	var payload bytes.Buffer
	payload.WriteString(query)
	payload.WriteByte(0)
	if err := pg.writeMessage('Q', payload.Bytes()); err != nil {
		return Result{}, err
	}
	result := Result{}
	for {
		msgType, payload, err := pg.readMessage()
		if err != nil {
			return Result{}, err
		}
		switch msgType {
		case 'T':
			columns, err := decodeRowDescription(payload)
			if err != nil {
				return Result{}, err
			}
			result.Columns = columns
		case 'D':
			values, err := decodeDataRow(payload)
			if err != nil {
				return Result{}, err
			}
			result.Rows = append(result.Rows, values)
		case 'C':
			result.CommandTag = strings.TrimRight(string(payload), "\x00")
		case 'E':
			return Result{}, decodeError(payload)
		case 'Z':
			return result, nil
		}
	}
}

func decodeRowDescription(payload []byte) ([]string, error) {
	reader := bytes.NewReader(payload)
	var count int16
	if err := binary.Read(reader, binary.BigEndian, &count); err != nil {
		return nil, err
	}
	columns := make([]string, 0, count)
	for i := 0; i < int(count); i++ {
		name, err := readCString(reader)
		if err != nil {
			return nil, err
		}
		columns = append(columns, name)
		skip := make([]byte, 18)
		if _, err := io.ReadFull(reader, skip); err != nil {
			return nil, err
		}
	}
	return columns, nil
}

func decodeDataRow(payload []byte) ([]string, error) {
	reader := bytes.NewReader(payload)
	var count int16
	if err := binary.Read(reader, binary.BigEndian, &count); err != nil {
		return nil, err
	}
	row := make([]string, 0, count)
	for i := 0; i < int(count); i++ {
		length, err := readInt32(reader)
		if err != nil {
			return nil, err
		}
		if length == -1 {
			row = append(row, "")
			continue
		}
		data := make([]byte, length)
		if _, err := io.ReadFull(reader, data); err != nil {
			return nil, err
		}
		row = append(row, string(data))
	}
	return row, nil
}

func readCString(r io.Reader) (string, error) {
	var buf bytes.Buffer
	one := make([]byte, 1)
	for {
		if _, err := io.ReadFull(r, one); err != nil {
			return "", err
		}
		if one[0] == 0 {
			return buf.String(), nil
		}
		buf.WriteByte(one[0])
	}
}

func decodeError(payload []byte) error {
	reader := bytes.NewReader(payload)
	fields := map[byte]string{}
	for {
		kind := make([]byte, 1)
		if _, err := io.ReadFull(reader, kind); err != nil {
			return err
		}
		if kind[0] == 0 {
			break
		}
		value, err := readCString(reader)
		if err != nil {
			return err
		}
		fields[kind[0]] = value
	}
	if message := fields['M']; message != "" {
		return errors.New(message)
	}
	return fmt.Errorf("postgres error")
}

func (pg *pgConn) terminate() { _ = pg.writeMessage('X', nil) }

func formatQuery(query string, args ...any) (string, error) {
	formatted := query
	tokens := make([]string, len(args))
	literals := make([]string, len(args))
	for idx := len(args); idx >= 1; idx-- {
		literal, err := sqlLiteral(args[idx-1])
		if err != nil {
			return "", err
		}
		token := fmt.Sprintf("__pg_arg_%d__", idx)
		tokens[idx-1] = token
		literals[idx-1] = literal
		formatted = strings.ReplaceAll(formatted, "$"+strconv.Itoa(idx), token)
	}
	for idx := range tokens {
		formatted = strings.ReplaceAll(formatted, tokens[idx], literals[idx])
	}
	return formatted, nil
}

func sqlLiteral(value any) (string, error) {
	switch typed := value.(type) {
	case nil:
		return "NULL", nil
	case string:
		return quoteLiteral(typed), nil
	case []byte:
		return quoteLiteral(string(typed)), nil
	case int:
		return strconv.Itoa(typed), nil
	case int8, int16, int32, int64:
		return fmt.Sprintf("%d", typed), nil
	case uint, uint8, uint16, uint32, uint64:
		return fmt.Sprintf("%d", typed), nil
	case float32, float64:
		return fmt.Sprintf("%v", typed), nil
	case bool:
		if typed {
			return "true", nil
		}
		return "false", nil
	case JSONBArg:
		raw, err := json.Marshal(typed.Value)
		if err != nil {
			return "", err
		}
		return quoteLiteral(string(raw)) + "::jsonb", nil
	case json.RawMessage:
		return quoteLiteral(string(typed)) + "::jsonb", nil
	default:
		return quoteLiteral(fmt.Sprint(typed)), nil
	}
}

func quoteLiteral(text string) string {
	return "'" + strings.ReplaceAll(text, "'", "''") + "'"
}
