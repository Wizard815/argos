package server

import (
	"io"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/logmon"
	"github.com/mostlygeek/llama-swap/internal/process"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// A multi_model base id never runs itself — the selector rewrites every request
// to a copy — so its own monitor stays empty and its Logs panel showed nothing.
// The stream has to come from the running copies instead.
func TestServer_LogSourceMergesRunningCopies(t *testing.T) {
	cfg := modelTogglesTestConfig(t, "")
	local := newStubRouter([]string{"real", "real--mm0", "real--mm1"}, "ok")
	local.loggers = map[string]*logmon.Monitor{
		"real--mm0": logmon.NewWriter(io.Discard),
		"real--mm1": logmon.NewWriter(io.Discard),
	}
	local.running = map[string]process.ProcessState{
		"real--mm0": process.StateReady,
		"real--mm1": process.StateReady,
	}
	s := profileTestServer(t, cfg, local)
	s.modelModesMu.Lock()
	s.modelModes = map[string]string{"real": ModeMultiModel}
	s.modelModesMu.Unlock()

	src, err := s.logSourceFor("real")
	require.NoError(t, err)
	merged, ok := src.(*multiModelLogs)
	require.True(t, ok, "a multi_model base must merge its copies, not use one monitor")
	assert.Equal(t, []string{"real--mm0", "real--mm1"}, merged.ids)
}

// Only copies that are actually up are merged: a stopped copy would contribute
// stale history and a dead subscription.
func TestServer_LogSourceSkipsStoppedCopies(t *testing.T) {
	cfg := modelTogglesTestConfig(t, "")
	local := newStubRouter([]string{"real", "real--mm0", "real--mm1"}, "ok")
	local.loggers = map[string]*logmon.Monitor{
		"real--mm0": logmon.NewWriter(io.Discard),
		"real--mm1": logmon.NewWriter(io.Discard),
	}
	local.running = map[string]process.ProcessState{"real--mm1": process.StateReady}
	s := profileTestServer(t, cfg, local)
	s.modelModesMu.Lock()
	s.modelModes = map[string]string{"real": ModeMultiModel}
	s.modelModesMu.Unlock()

	src, err := s.logSourceFor("real")
	require.NoError(t, err)
	merged, ok := src.(*multiModelLogs)
	require.True(t, ok)
	assert.Equal(t, []string{"real--mm1"}, merged.ids)
}

// Anything that is not a multi_model base keeps the existing single-monitor
// path, so an ordinary model's panel is unchanged.
func TestServer_LogSourceFallsBackForStandardModel(t *testing.T) {
	cfg := modelTogglesTestConfig(t, "")
	local := newStubRouter([]string{"real"}, "ok")
	local.loggers = map[string]*logmon.Monitor{"real": logmon.NewWriter(io.Discard)}
	s := profileTestServer(t, cfg, local)

	src, err := s.logSourceFor("real")
	require.NoError(t, err)
	_, merged := src.(*multiModelLogs)
	assert.False(t, merged, "a standard model must keep the single-monitor path")
}
