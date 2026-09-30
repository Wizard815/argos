package server

import (
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
	"github.com/mostlygeek/llama-swap/internal/process"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// stateOf returns the state modelStatus() reports for a model id.
func stateOf(t *testing.T, s *Server, id string) string {
	t.Helper()
	for _, m := range s.modelStatus() {
		if m.Id == id {
			return m.State
		}
	}
	t.Fatalf("model %q missing from modelStatus()", id)
	return ""
}

func TestServer_ModelStatusFollowsPinnedVariant(t *testing.T) {
	cfg := profileTestConfig(t) // the coding profile pins real -> hidden
	local := newStubRouter([]string{"real", "hidden"}, "ok")
	// Exactly what a pinned load produces: the variant is the process that runs
	// and the base model was never started.
	local.running = map[string]process.ProcessState{"hidden": process.StateReady}
	s := profileTestServer(t, cfg, local)

	assert.Equal(t, "stopped", stateOf(t, s, "real"), "with no profile the base id decides")

	_, err := s.setActiveProfile("coding")
	require.NoError(t, err)

	assert.Equal(t, "ready", stateOf(t, s, "real"),
		"a model pinned to a running variant must not read as stopped")
}

func TestServer_ModelStatusFallsBackToBaseModel(t *testing.T) {
	cfg := profileTestConfig(t)
	local := newStubRouter([]string{"real", "hidden"}, "ok")
	// The base model itself is loaded, the variant is not: the pin is in effect
	// but nothing is serving it, so the row has to follow the base model.
	local.running = map[string]process.ProcessState{"real": process.StateReady}
	s := profileTestServer(t, cfg, local)

	_, err := s.setActiveProfile("coding")
	require.NoError(t, err)

	assert.Equal(t, "ready", stateOf(t, s, "real"),
		"a directly loaded model stays up while a pin is active")
}

func TestServer_ModelStatus_ReportsTheBaseModelForAVariant(t *testing.T) {
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
models:
  ornith:
    cmd: echo ${PORT}
    name: Ornith 1.5 35B
  ornith--fast:
    cmd: echo ${PORT}
    name: Ornith 1.5 35B
    unlisted: true
`))
	require.NoError(t, err)
	s := profileTestServer(t, cfg, newStubRouter([]string{"ornith", "ornith--fast"}, "ok"))

	byID := make(map[string]apiModel)
	for _, m := range s.modelStatus() {
		byID[m.Id] = m
	}
	require.Contains(t, byID, "ornith--fast")
	assert.Equal(t, "ornith", byID["ornith--fast"].BaseModelId,
		"a variant has to name the model it belongs to")
	assert.Empty(t, byID["ornith"].BaseModelId,
		"a plain model is not a variant of anything")
}
