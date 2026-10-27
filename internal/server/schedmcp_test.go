package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Wizard815/Argos/internal/mcptools"
)

// Another model, or a dashboard, reads what the GPUs are doing through MCP.
func TestServer_ScheduleMCP_ReportsRunningAndRecentRequests(t *testing.T) {
	s := newMCPServer()
	registry, err := mcptools.New(&scheduleProvider{s: s})
	if err != nil {
		t.Fatal(err)
	}
	s.tools = registry

	pool := callToolRPC(t, s, "schedule__pool_status", `{}`)
	if pool.IsError || !strings.Contains(pool.text(), `"enabled": false`) {
		t.Fatalf("pool_status without the pool router = %q", pool.text())
	}

	_, cancel := context.WithCancel(context.Background())
	defer cancel()
	id := s.inflight.Add(httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil), cancel)
	running := callToolRPC(t, s, "schedule__requests_in_flight", `{}`)
	if !strings.Contains(running.text(), `"req_path": "/v1/chat/completions"`) {
		t.Fatalf("requests_in_flight = %q", running.text())
	}
	s.inflight.Remove(id)

	for i, model := range []string{"old", "new"} {
		if _, ok := s.metrics.queueMetrics(ActivityLogEntry{Timestamp: time.Unix(int64(i+1), 0), Model: model,
			Tokens: TokenMetrics{InputTokens: 19358, OutputTokens: 40, PromptPerSecond: 470}}); !ok {
			t.Fatal("queueMetrics failed")
		}
	}
	recent := callToolRPC(t, s, "schedule__recent_requests", `{"limit":1}`)
	text := recent.text()
	if !strings.Contains(text, `"model": "new"`) || strings.Contains(text, `"model": "old"`) || !strings.Contains(text, `"input_tokens": 19358`) {
		t.Fatalf("recent_requests limit 1 = %q, want only the newest", text)
	}
	if bad := callToolRPC(t, s, "schedule__recent_requests", `{"limit":500}`); !bad.IsError {
		t.Fatalf("limit 500 should be refused, got %q", bad.text())
	}
}
