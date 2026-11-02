package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// A client the loop guard stopped gets 429 at once for that model, with the
// reason; other clients and other models are untouched, and lifting the block
// lets it through again.
func TestLoopGuard_StopsOneClientForOneModel(t *testing.T) {
	s := newTestServer(newStubRouter([]string{"m1", "m2"}, ""))
	send := func(model, ip string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		r := chatRequest(model)
		r.Header.Set("X-Forwarded-For", ip)
		s.ServeHTTP(w, r)
		return w
	}
	if w := send("m1", "192.168.20.7"); w.Code == http.StatusTooManyRequests {
		t.Fatalf("not blocked yet: %d", w.Code)
	}

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/api/guard",
		strings.NewReader(`{"src":"192.168.20.7","model":"m1","reason":"40 requests of 19 in / 24 out in a row"}`)))
	if w.Code != http.StatusOK {
		t.Fatalf("block: %d %s", w.Code, w.Body.String())
	}

	stopped := send("m1", "192.168.20.7")
	if stopped.Code != http.StatusTooManyRequests || !strings.Contains(stopped.Body.String(), "19 in / 24 out") {
		t.Fatalf("want 429 with the reason, got %d %q", stopped.Code, stopped.Body.String())
	}
	if w := send("m1", "10.0.0.9"); w.Code == http.StatusTooManyRequests {
		t.Fatal("another client is not stopped")
	}
	if w := send("m2", "192.168.20.7"); w.Code == http.StatusTooManyRequests {
		t.Fatal("another model is not stopped")
	}

	list := httptest.NewRecorder()
	s.ServeHTTP(list, httptest.NewRequest(http.MethodGet, "/api/guard", nil))
	var got struct{ Blocks []loopBlock }
	if err := json.Unmarshal(list.Body.Bytes(), &got); err != nil || len(got.Blocks) != 1 || got.Blocks[0].Refused != 1 {
		t.Fatalf("list: %v %+v", err, got)
	}

	s.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodDelete, "/api/guard?src=192.168.20.7&model=m1", nil))
	if w := send("m1", "192.168.20.7"); w.Code == http.StatusTooManyRequests {
		t.Fatal("let run again: not stopped")
	}
}
