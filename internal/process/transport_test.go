package process

import (
	"net/http"
	"testing"

	"github.com/Wizard815/Argos/internal/config"
)

func TestProcessCommand_DisableKeepAlives(t *testing.T) {
	on, off := true, false
	for _, c := range []struct {
		v    *bool
		want bool
	}{{nil, false}, {&off, false}, {&on, true}} {
		cfg := config.ModelConfig{DisableKeepAlives: c.v}
		if got := newUpstreamTransport(cfg).DisableKeepAlives; got != c.want {
			t.Errorf("disableKeepAlives=%v: transport has %v", c.v, got)
		}
	}
}

func TestProcessCommand_StripBrowserHeaders(t *testing.T) {
	h := http.Header{}
	for _, k := range []string{"Origin", "Referer", "Sec-Fetch-Mode", "Sec-Fetch-Site", "Access-Control-Request-Method",
		"Authorization", "Content-Type", "User-Agent", "X-Forwarded-For"} {
		h.Set(k, "x")
	}
	stripBrowserHeaders(h)
	for _, gone := range []string{"Origin", "Referer", "Sec-Fetch-Mode", "Sec-Fetch-Site", "Access-Control-Request-Method"} {
		if h.Get(gone) != "" {
			t.Errorf("%s was passed on", gone)
		}
	}
	for _, kept := range []string{"Authorization", "Content-Type", "User-Agent", "X-Forwarded-For"} {
		if h.Get(kept) == "" {
			t.Errorf("%s was dropped", kept)
		}
	}
}
