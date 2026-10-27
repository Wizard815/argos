package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Wizard815/Argos/internal/router"
)

// A model that crashed three times in ten minutes is not started on a request:
// the client gets the line it died on. Loading it by hand clears that.
func TestServer_CrashLoopingModelIsNotStartedAgain(t *testing.T) {
	s := newTestServer(newStubRouter([]string{"crashy"}, "ok"))
	t.Cleanup(func() { router.Crashes.Clear("crashy") })
	for i := 0; i < 3; i++ {
		router.Crashes.State("crashy", "stopped", "starting")
		router.Crashes.State("crashy", "starting", "stopped")
	}

	ask := func() *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"crashy","messages":[]}`))
		req.Header.Set("Content-Type", "application/json")
		s.ServeHTTP(w, req)
		return w
	}
	w := ask()
	if w.Code != http.StatusServiceUnavailable || !strings.Contains(w.Body.String(), "crashed 3 times") {
		t.Fatalf("blocked model: status %d body %q", w.Code, w.Body.String())
	}

	w = httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/api/crashes/clear/crashy", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("clear: status %d", w.Code)
	}
	if w := ask(); w.Code != http.StatusOK {
		t.Fatalf("after clearing: status %d body %q", w.Code, w.Body.String())
	}
}
