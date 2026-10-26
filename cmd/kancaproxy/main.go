package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type upstream struct {
	addr string // host:port
	auth string // Proxy-Authorization header value, empty if none
}

type pool struct {
	mu       sync.RWMutex
	upstreams []upstream
	next     uint64
}

func loadPool(path string) ([]upstream, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var ups []upstream
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		u, err := url.Parse(line)
		if err != nil {
			log.Printf("pool: skipping invalid line %q: %v", line, err)
			continue
		}
		host := u.Hostname()
		port := u.Port()
		if port == "" {
			port = "80"
		}
		var auth string
		if u.User != nil {
			pass, _ := u.User.Password()
			auth = "Basic " + base64Encode(u.User.Username()+":"+pass)
		}
		ups = append(ups, upstream{addr: net.JoinHostPort(host, port), auth: auth})
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return ups, nil
}

func (p *pool) reload(path string) {
	ups, err := loadPool(path)
	if err != nil {
		log.Printf("pool reload failed: %v", err)
		return
	}
	p.mu.Lock()
	p.upstreams = ups
	p.mu.Unlock()
	log.Printf("pool reloaded: %d upstreams", len(ups))
}

// pick returns up to n distinct upstreams in round-robin order.
func (p *pool) pick(n int) []upstream {
	p.mu.RLock()
	defer p.mu.RUnlock()
	total := len(p.upstreams)
	if total == 0 {
		return nil
	}
	start := atomic.AddUint64(&p.next, 1) - 1
	if n > total {
		n = total
	}
	out := make([]upstream, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, p.upstreams[(int(start)+i)%total])
	}
	return out
}

func base64Encode(s string) string {
	const charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	src := []byte(s)
	var b strings.Builder
	for i := 0; i < len(src); i += 3 {
		var n uint32
		rem := len(src) - i
		n = uint32(src[i]) << 16
		if rem > 1 {
			n |= uint32(src[i+1]) << 8
		}
		if rem > 2 {
			n |= uint32(src[i+2])
		}
		b.WriteByte(charset[(n>>18)&0x3F])
		b.WriteByte(charset[(n>>12)&0x3F])
		if rem > 1 {
			b.WriteByte(charset[(n>>6)&0x3F])
		} else {
			b.WriteByte('=')
		}
		if rem > 2 {
			b.WriteByte(charset[n&0x3F])
		} else {
			b.WriteByte('=')
		}
	}
	return b.String()
}

// connectVia dials the upstream, issues CONNECT, and verifies a 200 response.
func connectVia(ctx context.Context, up upstream, target string) (net.Conn, error) {
	d := net.Dialer{}
	conn, err := d.DialContext(ctx, "tcp", up.addr)
	if err != nil {
		return nil, fmt.Errorf("dial %s: %w", up.addr, err)
	}

	req := "CONNECT " + target + " HTTP/1.1\r\nHost: " + target + "\r\n"
	if up.auth != "" {
		req += "Proxy-Authorization: " + up.auth + "\r\n"
	}
	req += "\r\n"

	// Use a write deadline based on the context.
	deadline, ok := ctx.Deadline()
	if !ok {
		deadline = time.Now().Add(15 * time.Second)
	}
	conn.SetDeadline(deadline)

	if _, err := conn.Write([]byte(req)); err != nil {
		conn.Close()
		return nil, fmt.Errorf("write CONNECT to %s: %w", up.addr, err)
	}

	br := bufio.NewReader(conn)
	statusLine, err := br.ReadString('\n')
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("read status from %s: %w", up.addr, err)
	}
	if !strings.Contains(statusLine, "200") {
		conn.Close()
		return nil, fmt.Errorf("%s refused CONNECT %s: %s", up.addr, target, strings.TrimSpace(statusLine))
	}
	// Drain remaining response headers.
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			conn.Close()
			return nil, fmt.Errorf("read headers from %s: %w", up.addr, err)
		}
		if strings.TrimSpace(line) == "" {
			break
		}
	}
	// Clear the deadline for relay; wrap conn with br to preserve any buffered bytes.
	conn.SetDeadline(time.Time{})
	return clientConn{conn, br}, nil
}

func relay(a, b net.Conn) {
	done := make(chan struct{}, 2)
	cp := func(dst io.Writer, src io.Reader) {
		io.Copy(dst, src)
		if tc, ok := dst.(*net.TCPConn); ok {
			tc.CloseWrite()
		}
		done <- struct{}{}
	}
	go cp(a, b)
	go cp(b, a)
	<-done
	<-done
}

func handleConnect(client net.Conn, p *pool) {
	defer client.Close()

	br := bufio.NewReader(client)
	reqLine, err := br.ReadString('\n')
	if err != nil {
		return
	}
	parts := strings.Fields(strings.TrimSpace(reqLine))
	if len(parts) != 3 || !strings.EqualFold(parts[0], "CONNECT") {
		fmt.Fprintf(client, "HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
		return
	}
	target := parts[1]
	// Drain request headers from client.
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			return
		}
		if strings.TrimSpace(line) == "" {
			break
		}
	}

	candidates := p.pick(3)
	if len(candidates) == 0 {
		fmt.Fprintf(client, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
		return
	}

	var upstreamConn net.Conn
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	var lastErr error
	for _, up := range candidates {
		upstreamConn, lastErr = connectVia(ctx, up, target)
		if lastErr == nil {
			cancel()
			break
		}
		log.Printf("upstream %s failed for %s: %v", up.addr, target, lastErr)
	}
	cancel()
	if upstreamConn == nil {
		log.Printf("all upstreams failed for %s: %v", target, lastErr)
		fmt.Fprintf(client, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
		return
	}

	if _, err := client.Write([]byte("HTTP/1.1 200 Connection established\r\n\r\n")); err != nil {
		upstreamConn.Close()
		return
	}
	// Any bytes the client pipelined after CONNECT were consumed by br.
	relay(clientConn{client, br}, upstreamConn)
}

// clientConn wraps the client connection so buffered bytes are not lost.
type clientConn struct {
	net.Conn
	br *bufio.Reader
}

func (c clientConn) Read(b []byte) (int, error) { return c.br.Read(b) }

func main() {
	listen := flag.String("listen", "127.0.0.1:25346", "local listen address")
	poolFile := flag.String("pool", "/tmp/proxies.txt", "upstream proxy list file")
	flag.Parse()

	p := &pool{}
	p.reload(*poolFile)
	if len(p.pick(1)) == 0 {
		log.Fatalf("no valid upstreams in %s", *poolFile)
	}

	go func() {
		t := time.NewTicker(5 * time.Minute)
		defer t.Stop()
		for range t.C {
			p.reload(*poolFile)
		}
	}()

	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatalf("listen %s: %v", *listen, err)
	}
	log.Printf("listening on %s with pool %s", *listen, *poolFile)

	for {
		conn, err := ln.Accept()
		if err != nil {
			log.Printf("accept: %v", err)
			continue
		}
		go handleConnect(conn, p)
	}
}
