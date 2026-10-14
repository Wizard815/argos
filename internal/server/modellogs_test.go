package server

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/logmon"
	"github.com/Wizard815/Argos/internal/process"
)

// modelLogServer is a server with one model ("m1") that is running and has
// already written a line, which is the state a log panel reads.
func modelLogServer(t *testing.T) *Server {
	t.Helper()

	lg := logmon.NewWriter(io.Discard)
	if _, err := lg.Write([]byte("history line\n")); err != nil {
		t.Fatalf("seed log: %v", err)
	}

	stub := newStubRouter(nil, "")
	stub.loggers = map[string]*logmon.Monitor{"m1": lg}
	stub.running = map[string]process.ProcessState{"m1": process.ProcessState("ready")}

	s := newTestServer(stub)
	s.cfg = config.Config{Models: map[string]config.ModelConfig{"m1": {Name: "m1"}}}
	return s
}

// A log page wants what a model has printed, now, in one request. The streaming
// endpoint cannot serve that: it holds the connection open after the history.
func TestServer_HandleModelLogs_ReturnsOneMonitorsHistory(t *testing.T) {
	s := modelLogServer(t)

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/logs/m1", nil))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %q", w.Code, w.Body.String())
	}
	if got := w.Body.String(); got != "history line\n" {
		t.Errorf("body = %q, want the monitor's history", got)
	}
	if got := w.Header().Get("Content-Type"); !strings.HasPrefix(got, "text/plain") {
		t.Errorf("Content-Type = %q, want text/plain", got)
	}
}

// The wildcard route must not swallow the streaming routes: "/logs/stream/m1"
// has to keep reaching handleLogStream. If it fell through to handleModelLogs
// the monitor id would be "stream/m1" and the request would fail instead.
func TestServer_HandleModelLogs_StreamRouteStillWins(t *testing.T) {
	s := modelLogServer(t)

	// A cancelled context ends the stream handler right after it writes the
	// history it already has, which is the part under test here.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/logs/stream/m1", nil).WithContext(ctx))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %q", w.Code, w.Body.String())
	}
	if got := w.Body.String(); got != "history line\n" {
		t.Errorf("body = %q, want the streaming handler's history", got)
	}
}

// An id that names no monitor is a client error, not an empty 200, so a typo in
// a log page shows up as an error instead of a blank panel.
func TestServer_HandleModelLogs_UnknownMonitorIsRejected(t *testing.T) {
	s := modelLogServer(t)

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/logs/no-such-model", nil))

	if w.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", w.Code)
	}
}

// The exact "/logs" path keeps returning the combined history; the new
// wildcard route must not take it over.
func TestServer_HandleLogs_CombinedHistoryIsNotShadowed(t *testing.T) {
	s := modelLogServer(t)
	if _, err := s.muxlog.Write([]byte("mux line\n")); err != nil {
		t.Fatalf("seed mux log: %v", err)
	}

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/logs", nil))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "mux line") {
		t.Errorf("body = %q, want the combined history", w.Body.String())
	}
}
