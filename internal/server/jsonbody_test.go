package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestServer_JSONBodyWithoutContentType(t *testing.T) {
	r := newStubRouter([]string{"m"}, "ok")
	s := newTestServer(r)
	for _, c := range []struct {
		ctype, body string
		want        int
	}{
		{"", `{"model":"m"}`, http.StatusOK},
		{"text/plain", ` {"model":"m"}`, http.StatusOK},
		{"application/x-www-form-urlencoded", `{"model":"m"}`, http.StatusOK}, // curl -d
		{"application/json; charset=utf-8", `{"model":"m"}`, http.StatusOK},
		{"text/plain", `model=m`, http.StatusBadRequest},
		{"", ``, http.StatusBadRequest},
	} {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(c.body))
		if c.ctype != "" {
			req.Header.Set("Content-Type", c.ctype)
		}
		w := httptest.NewRecorder()
		s.ServeHTTP(w, req)
		if w.Code != c.want {
			t.Errorf("Content-Type %q body %q: status=%d %s, want %d", c.ctype, c.body, w.Code, w.Body.String(), c.want)
		}
	}
}

func TestServer_BrokenJSONIsA400(t *testing.T) {
	h := requireJSONBody(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(200) }))
	for _, body := range []string{"{not json", "", "[1,2]", `"text"`, `{"messages":[]}`} {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("%q: status %d, want 400", body, w.Code)
		}
	}
}
