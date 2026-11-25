package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"unicode/utf8"
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

// Two agents on one machine share an address: a block for one app (its
// User-Agent) stops it and leaves the other alone.
func TestLoopGuard_StopsOneAppOnASharedAddress(t *testing.T) {
	s := newTestServer(newStubRouter([]string{"m1"}, ""))
	send := func(ua string) int {
		w := httptest.NewRecorder()
		r := chatRequest("m1")
		r.Header.Set("X-Forwarded-For", "192.168.20.7")
		r.Header.Set("User-Agent", ua)
		s.ServeHTTP(w, r)
		return w.Code
	}
	s.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/api/guard",
		strings.NewReader(`{"src":"192.168.20.7","client":"node","model":"m1","reason":"61 out every time"}`)))
	if code := send("node"); code != http.StatusTooManyRequests {
		t.Fatalf("Codex (node): want 429, got %d", code)
	}
	if code := send("OpenAI/Python 2.24.0"); code == http.StatusTooManyRequests {
		t.Fatal("Hermes (OpenAI/Python) on the same address must not be stopped")
	}
	s.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodDelete, "/api/guard?src=192.168.20.7&client=node&model=m1", nil))
	if code := send("node"); code == http.StatusTooManyRequests {
		t.Fatal("lifted")
	}
}

// Behind LiteLLM every app comes from one address with one Argos key and the
// same User-Agent (LiteLLM's own); the builder names each app (X-Argos-App,
// from LiteLLM's key alias or the app's X-Title), and one app's loop stops
// only that app.
func TestLoopGuard_StopsOneAppBehindLiteLLM(t *testing.T) {
	s := newTestServer(newStubRouter([]string{"m1"}, ""))
	send := func(app string) int {
		w := httptest.NewRecorder()
		r := chatRequest("m1")
		r.Header.Set("X-Forwarded-For", "10.0.0.5")
		r.Header.Set("User-Agent", "AsyncOpenAI/Python 1.99.1")
		r.Header.Set("X-Argos-Key", "litellm")
		r.Header.Set("X-Argos-App", app)
		s.ServeHTTP(w, r)
		return w.Code
	}
	s.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/api/guard",
		strings.NewReader(`{"src":"10.0.0.5","client":"litellm / codex","model":"m1","reason":"61 out every time"}`)))
	if code := send("codex"); code != http.StatusTooManyRequests {
		t.Fatalf("codex: want 429, got %d", code)
	}
	w := httptest.NewRecorder()
	r := chatRequest("m1")
	r.Header.Set("X-Forwarded-For", "10.0.0.5")
	r.Header.Set("X-Argos-Key", "litellm")
	r.Header.Set("X-Argos-App", "codex")
	s.ServeHTTP(w, r)
	if !strings.Contains(w.Body.String(), "litellm / codex's requests") {
		t.Errorf("the 429 names the app: %s", w.Body.String())
	}
	if code := send("hermes"); code == http.StatusTooManyRequests {
		t.Fatal("hermes behind the same LiteLLM must not be stopped")
	}
}

func TestLoopGuard_AppLabel(t *testing.T) {
	for _, c := range []struct{ key, app, ua, want string }{
		{"", "", "node", "node"},
		{"litellm", "codex", "AsyncOpenAI/Python 1.99", "litellm / codex"},
		{"litellm", "", "AsyncOpenAI/Python 1.99", "litellm / AsyncOpenAI/Python 1.99"},
		{"hermes", "", "", "hermes"},
		{"", "hermes", "x", "hermes"},
		{"", "", "", ""},
	} {
		if got := appLabel(c.key, c.app, c.ua); got != c.want {
			t.Errorf("appLabel(%q, %q, %q) = %q, want %q", c.key, c.app, c.ua, got, c.want)
		}
	}
	if got := appLabel("k", strings.Repeat("a", 200), ""); len(got) != 80 {
		t.Errorf("long labels are cut to 80, got %d", len(got))
	}
	if got := appLabel("", strings.Repeat("ü", 60), ""); !utf8.ValidString(got) || len(got) > 80 {
		t.Errorf("a cut never splits a character: %q", got)
	}
}
