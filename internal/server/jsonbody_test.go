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
