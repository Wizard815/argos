package router

import (
	"net/http/httptest"
	"testing"
)

// Apps take turns in a model's line by ClientOf: an API key, and the app
// behind it when the builder made one out (several apps behind one LiteLLM
// key still take turns), else the address and that app.
func TestRouter_ClientOf(t *testing.T) {
	for _, c := range []struct {
		key, app, xff, want string
	}{
		{"hermes", "", "", "key:hermes"},
		{"litellm", "codex", "10.0.0.5", "key:litellm|codex"},
		{"", "codex", "10.0.0.5, 127.0.0.1", "ip:10.0.0.5|codex"},
		{"", "", "10.0.0.5", "ip:10.0.0.5"},
	} {
		r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
		if c.key != "" {
			r.Header.Set("X-Argos-Key", c.key)
		}
		if c.app != "" {
			r.Header.Set("X-Argos-App", c.app)
		}
		if c.xff != "" {
			r.Header.Set("X-Forwarded-For", c.xff)
		}
		if got := ClientOf(r); got != c.want {
			t.Errorf("ClientOf(key=%q app=%q xff=%q) = %q, want %q", c.key, c.app, c.xff, got, c.want)
		}
	}
}
