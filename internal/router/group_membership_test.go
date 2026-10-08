package router

import (
	"io"
	"testing"
	"time"

	"github.com/mostlygeek/llama-swap/internal/config"
	"github.com/mostlygeek/llama-swap/internal/logmon"
)

// A group that names a model with no model config must still build. Group
// membership is configuration, and that list drifts as models are added and
// removed; refusing to start over one stale name takes every other model down
// with it, which is what happened in practice.
//
// A model listed in two groups is still an error here, and also at config load
// (internal/config/load.go), because that is a conflict rather than drift.
func TestGroup_UnknownMemberIsIgnored(t *testing.T) {
	conf := config.Config{
		HealthCheckTimeout: 5,
		Models: map[string]config.ModelConfig{
			"present": {Cmd: "echo hi"},
		},
		Routing: config.RoutingConfig{
			Router: config.RouterConfig{
				Settings: config.RouterSettings{
					Groups: map[string]config.GroupConfig{
						"g": {Members: []string{"present", "deleted-model"}},
					},
				},
			},
		},
	}

	g, err := NewGroup(conf, logmon.NewWriter(io.Discard), logmon.NewWriter(io.Discard))
	if err != nil {
		t.Fatalf("NewGroup must tolerate a member with no model config, got: %v", err)
	}
	t.Cleanup(func() {
		if !g.shuttingDown.Load() {
			_ = g.Shutdown(time.Second)
		}
	})

	// A freshly built router reports nothing as running - processes start on
	// demand - so assert on membership: the resolvable member is handled, and
	// the ignored one is not.
	if !g.Handles("present") {
		t.Errorf("the resolvable member should still be handled")
	}
	if g.Handles("deleted-model") {
		t.Errorf("the member with no model config should not be handled")
	}
}
