package server

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/logmon"
	"github.com/mostlygeek/llama-swap/internal/process"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// The UI addresses a model by the id it displays. With a pin active the process
// that serves that id is the variant, so everything that reaches for the process
// - the log panel, the unload button - has to resolve the id the same way the
// request pipeline and the model card do.

func TestServer_LogStreamFollowsPinnedVariant(t *testing.T) {
	cfg := profileTestConfig(t) // the coding profile pins real -> hidden
	local := newStubRouter([]string{"real", "hidden"}, "ok")
	variantLog := logmon.NewWriter(io.Discard)
	local.loggers = map[string]*logmon.Monitor{"hidden": variantLog}
	s := profileTestServer(t, cfg, local)

	_, err := s.getLogger("real")
	require.Error(t, err, "with no profile the base id has no process of its own")

	_, err = s.setActiveProfile("coding")
	require.NoError(t, err)

	got, err := s.getLogger("real")
	require.NoError(t, err, "the pinned variant's log has to answer for the base id")
	assert.Same(t, variantLog, got)
}

func TestServer_UnloadFollowsPinnedVariant(t *testing.T) {
	cfg := profileTestConfig(t)
	local := newStubRouter([]string{"real", "hidden"}, "ok")
	// what a pinned load leaves behind: the variant runs, the base never did
	local.running = map[string]process.ProcessState{"hidden": process.StateReady}
	s := profileTestServer(t, cfg, local)
	_, err := s.setActiveProfile("coding")
	require.NoError(t, err)

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/api/models/unload/real", nil))
	require.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, []string{"hidden"}, local.unloadModels,
		"unloading the pinned card must stop the variant that is serving")
}

func TestServer_UnloadPrefersRunningBaseOverIdlePin(t *testing.T) {
	cfg := profileTestConfig(t)
	local := newStubRouter([]string{"real", "hidden"}, "ok")
	// the pin is in effect but the base model itself is what is loaded
	local.running = map[string]process.ProcessState{"real": process.StateReady}
	s := profileTestServer(t, cfg, local)
	_, err := s.setActiveProfile("coding")
	require.NoError(t, err)

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/api/models/unload/real", nil))
	require.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, []string{"real"}, local.unloadModels,
		"a directly loaded base model stays stoppable while a pin is active")
}
