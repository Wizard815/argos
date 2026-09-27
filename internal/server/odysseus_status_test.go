package server

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
)

// TestServer_ModelStatus_IncludesLaunchConfig guards the data behind the UI's
// model detail view.
//
// The detail route reads the `models` store, which is fed by the modelStatus SSE
// event -- NOT by /v1/models. So the launch configuration has to be on this
// payload as well, or the Launch command panel reports "no launch command
// reported" even though /v1/models carries it.
func TestServer_ModelStatus_IncludesLaunchConfig(t *testing.T) {
	cfg := config.Config{Models: map[string]config.ModelConfig{
		"with-cmd": {
			Cmd: "llama-server -m /models/a.gguf --port ${PORT} -lm dio",
			Env: []string{"HIP_VISIBLE_DEVICES=0"},
		},
		"without-cmd": {},
	}}
	s := newTestServerWithConfig(cfg, &stubRouter{}, &stubRouter{})

	statuses := s.modelStatus()
	byID := make(map[string]apiModel, len(statuses))
	for _, m := range statuses {
		byID[m.Id] = m
	}
	if len(byID) != 2 {
		t.Fatalf("modelStatus() returned %d models, want 2", len(byID))
	}

	got, ok := byID["with-cmd"]
	if !ok {
		t.Fatal("model missing from modelStatus()")
	}
	// the macro must be preserved: it is what the config says, and the UI
	// explains the substitution rather than doing it
	if !strings.Contains(got.Cmd, "${PORT}") {
		t.Errorf("Cmd = %q, want the ${PORT} macro preserved", got.Cmd)
	}
	if len(got.Env) != 1 || got.Env[0] != "HIP_VISIBLE_DEVICES=0" {
		t.Errorf("Env = %v, want [HIP_VISIBLE_DEVICES=0]", got.Env)
	}

	// and it has to survive the JSON frame the SSE event actually sends
	raw, err := json.Marshal(statuses)
	if err != nil {
		t.Fatalf("marshalling modelStatus(): %v", err)
	}
	if !strings.Contains(string(raw), `"cmd":`) || !strings.Contains(string(raw), `"env":`) {
		t.Errorf("launch config missing from the marshalled payload: %s", raw)
	}

	// a model with no command must omit the fields rather than emit empties,
	// so the UI's fallback message still triggers
	empty, ok := byID["without-cmd"]
	if !ok {
		t.Fatal("second model missing from modelStatus()")
	}
	emptyRaw, err := json.Marshal(empty)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(emptyRaw), `"cmd"`) || strings.Contains(string(emptyRaw), `"env"`) {
		t.Errorf("model without a command emitted cmd/env: %s", emptyRaw)
	}
}
