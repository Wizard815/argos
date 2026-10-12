package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tailscale/tailcat"
	"tailscale.com/types/key"
)

func testTailcatBlob() string {
	private := tailcat.NewPrivateKey()
	private.Public.RegionID = 1
	return string(private.Public.Addr())
}

func writeTailcatKey(t *testing.T) string {
	t.Helper()
	private := tailcat.NewPrivateKey()
	data, err := json.Marshal(private)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "tailcat.private.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestConfig_TailcatValidation(t *testing.T) {
	client := key.NewNode().Public().String()
	yaml := fmt.Sprintf(`
models:
  local:
    proxy: http://localhost:1
    aliases: [alias]
selectors:
  select:
    strategy: pin
    targets: [local]
profiles:
  prod:
    pins:
      public: local
tailcat:
  allow: [%q, %q]
  models: [local, alias, select, public, "*", local]
  debug: true
`, client, client)
	cfg, err := LoadConfigFromReader(strings.NewReader(yaml))
	if err != nil {
		t.Fatalf("LoadConfigFromReader: %v", err)
	}
	if cfg.Tailcat == nil {
		t.Fatal("Tailcat config was not retained")
	}
	if !cfg.Tailcat.Debug {
		t.Fatal("Debug = false, want true")
	}
	if got := len(cfg.Tailcat.AllowedClients); got != 1 {
		t.Fatalf("AllowedClients = %d, want 1", got)
	}
	wantModels := []string{"local", "alias", "select", "public", "*"}
	if fmt.Sprint(cfg.Tailcat.Models) != fmt.Sprint(wantModels) {
		t.Fatalf("Models = %v, want %v", cfg.Tailcat.Models, wantModels)
	}
}

func TestConfig_TailcatRejectsInvalidValues(t *testing.T) {
	tests := []struct {
		name string
		yaml string
		want string
	}{
		{"bad allow", "tailcat:\n  allow: [abc]\nmodels: {}\n", "tailcat.allow[0]: invalid node public key"},
		{"unknown model with wildcard", "tailcat:\n  models: ['*', missing]\nmodels: {}\n", "tailcat.models[1]: unknown public model ID"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := LoadConfigFromReader(strings.NewReader(tt.yaml))
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("error = %v, want substring %q", err, tt.want)
			}
		})
	}
}
