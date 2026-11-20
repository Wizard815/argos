package config

import (
	"strings"
	"testing"
)

func TestConfig_DisableKeepAlivesDefaultAndOverride(t *testing.T) {
	cfg, err := LoadConfigFromReader(strings.NewReader(`
disableKeepAlives: true
models:
  a:
    cmd: llama-server --port ${PORT}
  b:
    cmd: llama-server --port ${PORT}
    disableKeepAlives: false
`))
	if err != nil {
		t.Fatal(err)
	}
	if v := cfg.Models["a"].DisableKeepAlives; v == nil || !*v {
		t.Errorf("a: %v, want the global true", v)
	}
	if v := cfg.Models["b"].DisableKeepAlives; v == nil || *v {
		t.Errorf("b: %v, want its own false", v)
	}
}
