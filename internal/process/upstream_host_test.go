package process

import (
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"testing"
)

func TestProcessCommand_UpstreamHost(t *testing.T) {
	for _, tc := range []struct{ proxy, want string }{
		{"https://openrouter.ai/api", "openrouter.ai"},
		{"https://api.example.test:8443", "api.example.test:8443"},
		{"http://127.0.0.1:10001", "argos.lan:8080"},
	} {
		target, _ := url.Parse(tc.proxy)
		rp := httputil.NewSingleHostReverseProxy(target)
		setUpstreamHost(rp, target)
		req := httptest.NewRequest("POST", "http://argos.lan:8080/v1/chat/completions", nil)
		rp.Director(req)
		if req.Host != tc.want {
			t.Errorf("%s: Host = %q, want %q", tc.proxy, req.Host, tc.want)
		}
		if req.URL.Host != target.Host {
			t.Errorf("%s: URL host = %q, want %q", tc.proxy, req.URL.Host, target.Host)
		}
	}
}
