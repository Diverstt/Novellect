package redisclient

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Host    string
	Port    int
	Timeout time.Duration
}
type Client struct{ cfg Config }

func New(cfg Config) *Client      { return &Client{cfg: cfg} }
func (c *Client) address() string { return net.JoinHostPort(c.cfg.Host, strconv.Itoa(c.cfg.Port)) }
func (c *Client) withConn(ctx context.Context, fn func(*conn) error) error {
	dialer := net.Dialer{Timeout: c.cfg.Timeout}
	raw, err := dialer.DialContext(ctx, "tcp", c.address())
	if err != nil {
		return err
	}
	defer raw.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = raw.SetDeadline(deadline)
	} else {
		_ = raw.SetDeadline(time.Now().Add(c.cfg.Timeout))
	}
	return fn(&conn{rw: bufio.NewReadWriter(bufio.NewReader(raw), bufio.NewWriter(raw))})
}
func (c *Client) Ping(ctx context.Context) error {
	return c.withConn(ctx, func(cc *conn) error {
		if err := cc.command("PING"); err != nil {
			return err
		}
		_, err := cc.readReply()
		return err
	})
}
func (c *Client) Get(ctx context.Context, key string) (string, bool, error) {
	var value string
	var ok bool
	err := c.withConn(ctx, func(cc *conn) error {
		if err := cc.command("GET", key); err != nil {
			return err
		}
		reply, err := cc.readReply()
		if err != nil {
			return err
		}
		if reply == nil {
			ok = false
			return nil
		}
		value, ok = reply.(string)
		return nil
	})
	return value, ok, err
}
func (c *Client) SetEX(ctx context.Context, key, value string, ttl time.Duration) error {
	return c.withConn(ctx, func(cc *conn) error {
		if err := cc.command("SETEX", key, strconv.Itoa(int(ttl.Seconds())), value); err != nil {
			return err
		}
		_, err := cc.readReply()
		return err
	})
}
func (c *Client) Del(ctx context.Context, keys ...string) error {
	if len(keys) == 0 {
		return nil
	}
	args := append([]string{"DEL"}, keys...)
	return c.withConn(ctx, func(cc *conn) error {
		if err := cc.command(args...); err != nil {
			return err
		}
		_, err := cc.readReply()
		return err
	})
}
func (c *Client) ScanPrefix(ctx context.Context, prefix string) ([]string, error) {
	keys := []string{}
	cursor := "0"
	for {
		var batch []string
		err := c.withConn(ctx, func(cc *conn) error {
			if err := cc.command("SCAN", cursor, "MATCH", prefix+"*", "COUNT", "100"); err != nil {
				return err
			}
			reply, err := cc.readReply()
			if err != nil {
				return err
			}
			array, ok := reply.([]any)
			if !ok || len(array) != 2 {
				return fmt.Errorf("unexpected scan reply")
			}
			cursor, _ = array[0].(string)
			inner, _ := array[1].([]any)
			for _, item := range inner {
				if text, ok := item.(string); ok {
					batch = append(batch, text)
				}
			}
			return nil
		})
		if err != nil {
			return nil, err
		}
		keys = append(keys, batch...)
		if cursor == "0" {
			return keys, nil
		}
	}
}

type conn struct{ rw *bufio.ReadWriter }

func (c *conn) command(args ...string) error {
	if _, err := fmt.Fprintf(c.rw, "*%d\r\n", len(args)); err != nil {
		return err
	}
	for _, arg := range args {
		if _, err := fmt.Fprintf(c.rw, "$%d\r\n%s\r\n", len(arg), arg); err != nil {
			return err
		}
	}
	return c.rw.Flush()
}
func (c *conn) readLine() (string, error) {
	line, err := c.rw.ReadString('\n')
	if err != nil {
		return "", err
	}
	return strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r"), nil
}
func (c *conn) readReply() (any, error) {
	prefix, err := c.rw.ReadByte()
	if err != nil {
		return nil, err
	}
	switch prefix {
	case '+':
		return c.readLine()
	case '-':
		text, err := c.readLine()
		if err != nil {
			return nil, err
		}
		return nil, errors.New(text)
	case ':':
		text, err := c.readLine()
		if err != nil {
			return nil, err
		}
		value, err := strconv.ParseInt(text, 10, 64)
		if err != nil {
			return nil, err
		}
		return value, nil
	case '$':
		text, err := c.readLine()
		if err != nil {
			return nil, err
		}
		length, err := strconv.Atoi(text)
		if err != nil {
			return nil, err
		}
		if length == -1 {
			return nil, nil
		}
		data := make([]byte, length+2)
		if _, err := io.ReadFull(c.rw, data); err != nil {
			return nil, err
		}
		return string(data[:length]), nil
	case '*':
		text, err := c.readLine()
		if err != nil {
			return nil, err
		}
		count, err := strconv.Atoi(text)
		if err != nil {
			return nil, err
		}
		values := make([]any, 0, count)
		for i := 0; i < count; i++ {
			item, err := c.readReply()
			if err != nil {
				return nil, err
			}
			values = append(values, item)
		}
		return values, nil
	default:
		return nil, fmt.Errorf("unsupported redis reply prefix %q", string([]byte{prefix}))
	}
}
func EncodeArray(args ...string) []byte {
	var buf bytes.Buffer
	fmt.Fprintf(&buf, "*%d\r\n", len(args))
	for _, arg := range args {
		fmt.Fprintf(&buf, "$%d\r\n%s\r\n", len(arg), arg)
	}
	return buf.Bytes()
}
