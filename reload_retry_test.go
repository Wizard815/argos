package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestReloadRetry_ShutdownAnswerGoesToTheNewServer(t *testing.T) {
	var mu sync.Mutex
	var bodies []string
	old := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		mu.Lock()
		bodies = append(bodies, "old:"+string(b))
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"unspecific error: pool is shutting down"}}`))
	})
	fresh := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		mu.Lock()
		bodies = append(bodies, "new:"+string(b))
		mu.Unlock()
		w.Header().Set("X-New", "1")
		w.Write([]byte("answered"))
	})
	var cur http.Handler = old
	var gen uint64
	var cmu sync.Mutex
	go func() { time.Sleep(150 * time.Millisecond); cmu.Lock(); cur, gen = fresh, 1; cmu.Unlock() }()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"m"}`))
	w := httptest.NewRecorder()
	serveRetrying(w, req, func() (http.Handler, uint64) { cmu.Lock(); defer cmu.Unlock(); return cur, gen })
	if w.Code != 200 || w.Body.String() != "answered" || w.Header().Get("X-New") != "1" || w.Header().Get("Content-Type") == "application/json" {
		t.Fatalf("got %d %q %v", w.Code, w.Body.String(), w.Header())
	}
	if len(bodies) != 2 || bodies[1] != `new:{"model":"m"}` {
		t.Fatalf("bodies %v", bodies)
	}
}

func TestReloadRetry_OtherErrorsAndAnswersPassThrough(t *testing.T) {
	calls := 0
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("model crashed"))
	})
	w := httptest.NewRecorder()
	serveRetrying(w, httptest.NewRequest(http.MethodPost, "/v1/x", strings.NewReader("{}")), func() (http.Handler, uint64) { return h, 0 })
	if w.Code != 500 || w.Body.String() != "model crashed" || calls != 1 {
		t.Fatalf("got %d %q after %d calls", w.Code, w.Body.String(), calls)
	}
	empty := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusInternalServerError) })
	w = httptest.NewRecorder()
	serveRetrying(w, httptest.NewRequest(http.MethodGet, "/", nil), func() (http.Handler, uint64) { return empty, 0 })
	if w.Code != 500 {
		t.Fatalf("a held 500 with no body is still sent: %d", w.Code)
	}
	stream := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("data: 1\n\n"))
		w.(http.Flusher).Flush()
		w.Write([]byte("data: 2\n\n"))
	})
	w = httptest.NewRecorder()
	serveRetrying(w, httptest.NewRequest(http.MethodGet, "/", nil), func() (http.Handler, uint64) { return stream, 0 })
	if w.Body.String() != "data: 1\n\ndata: 2\n\n" || !w.Flushed {
		t.Fatalf("stream: %q flushed=%v", w.Body.String(), w.Flushed)
	}
}
