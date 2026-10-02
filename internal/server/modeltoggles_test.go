package server

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func modelTogglesTestConfig(t *testing.T, path string) config.Config {
	t.Helper()
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
models:
  real:
    cmd: echo ${PORT}
  other:
    cmd: echo ${PORT}
`))
	require.NoError(t, err)
	cfg.ModelTogglesFile = path
	return cfg
}

func TestServer_ModelModeDefaultsToStandard(t *testing.T) {
	path := filepath.Join(t.TempDir(), "model-toggles.json")
	s := profileTestServer(t, modelTogglesTestConfig(t, path), newStubRouter([]string{"real"}, "ok"))
	s.restoreModelToggles()

	assert.Equal(t, ModeStandard, s.ModelMode("real"))
	assert.Equal(t, ModeStandard, s.ModelMode("other"))
	assert.NoFileExists(t, path, "reading the default must not write a file")
}

func TestServer_ModelModePersistsAndRestores(t *testing.T) {
	path := filepath.Join(t.TempDir(), "model-toggles.json")
	s := profileTestServer(t, modelTogglesTestConfig(t, path), newStubRouter([]string{"real"}, "ok"))

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/models/real/mode",
		strings.NewReader(`{"mode":"single_gpu"}`))
	s.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code, w.Body.String())
	assert.Contains(t, w.Body.String(), `"mode":"single_gpu"`)

	assert.Equal(t, ModeSingleGPU, s.ModelMode("real"))

	data, err := os.ReadFile(path)
	require.NoError(t, err, "changing a mode must record it")
	assert.JSONEq(t, `{"modes":{"real":"single_gpu"}}`, string(data))

	// a restart must come back on the same mode
	fresh := profileTestServer(t, modelTogglesTestConfig(t, path), newStubRouter([]string{"real"}, "ok"))
	fresh.restoreModelToggles()
	assert.Equal(t, ModeSingleGPU, fresh.ModelMode("real"))
}

func TestServer_ModelModeStandardClearsEntry(t *testing.T) {
	path := filepath.Join(t.TempDir(), "model-toggles.json")
	require.NoError(t, os.WriteFile(path, []byte(`{"modes":{"real":"multi_model"}}`+"\n"), 0o644))

	s := profileTestServer(t, modelTogglesTestConfig(t, path), newStubRouter([]string{"real"}, "ok"))
	s.restoreModelToggles()
	require.Equal(t, ModeMultiModel, s.ModelMode("real"))

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/models/real/mode",
		strings.NewReader(`{"mode":"standard"}`))
	s.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code, w.Body.String())

	assert.Equal(t, ModeStandard, s.ModelMode("real"))
	data, err := os.ReadFile(path)
	require.NoError(t, err)
	assert.JSONEq(t, `{"modes":{}}`, string(data), "standard is the default; no key should remain")
}

func TestServer_ModelModeRejectsUnknownMode(t *testing.T) {
	path := filepath.Join(t.TempDir(), "model-toggles.json")
	s := profileTestServer(t, modelTogglesTestConfig(t, path), newStubRouter([]string{"real"}, "ok"))

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/models/real/mode",
		strings.NewReader(`{"mode":"warp_speed"}`))
	s.ServeHTTP(w, req)
	assert.Equal(t, http.StatusBadRequest, w.Code)
	assert.Equal(t, ModeStandard, s.ModelMode("real"), "a rejected mode must not be stored")
}

func TestServer_ModelModeUnknownModelIs404(t *testing.T) {
	path := filepath.Join(t.TempDir(), "model-toggles.json")
	s := profileTestServer(t, modelTogglesTestConfig(t, path), newStubRouter([]string{"real"}, "ok"))

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/models/nope/mode",
		strings.NewReader(`{"mode":"single_gpu"}`))
	s.ServeHTTP(w, req)
	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestServer_ModelModeDisabledWithoutPath(t *testing.T) {
	s := profileTestServer(t, modelTogglesTestConfig(t, ""), newStubRouter([]string{"real"}, "ok"))
	s.restoreModelToggles()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/models/real/mode",
		strings.NewReader(`{"mode":"single_gpu"}`))
	s.ServeHTTP(w, req)
	assert.Equal(t, http.StatusServiceUnavailable, w.Code,
		"an empty modelTogglesFile disables the control")
}
