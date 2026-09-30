package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func profileStateTestConfig(t *testing.T, stateFile string) config.Config {
	t.Helper()
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
models:
  real:
    cmd: echo ${PORT}
profiles:
  coding:
    description: Coding profile
    pins:
      real: real
`))
	require.NoError(t, err)
	cfg.ProfileStateFile = stateFile
	return cfg
}

func TestServer_ProfileStateFileRemembersChoice(t *testing.T) {
	path := filepath.Join(t.TempDir(), "profile-state.json")
	s := profileTestServer(t, profileStateTestConfig(t, path), newStubRouter([]string{"real"}, "ok"))

	s.restoreProfileState()
	assert.Empty(t, s.ActiveProfile(), "no state file and no hook means no active profile")

	_, err := s.setActiveProfile("coding")
	require.NoError(t, err)

	data, err := os.ReadFile(path)
	require.NoError(t, err, "changing the profile must record it")
	assert.JSONEq(t, `{"active":"coding"}`, string(data))
}

func TestServer_ProfileStateFileRestoresLastChoice(t *testing.T) {
	path := filepath.Join(t.TempDir(), "profile-state.json")
	require.NoError(t, os.WriteFile(path, []byte("{\"active\":\"coding\"}\n"), 0o644))

	cfg := profileStateTestConfig(t, path)
	cfg.Hooks.OnStartup.Profile = "" // no hook: the remembered name is the only source
	s := profileTestServer(t, cfg, newStubRouter([]string{"real"}, "ok"))
	s.restoreProfileState()

	assert.Equal(t, "coding", s.ActiveProfile())
}

func TestServer_ProfileStateFileRemembersDeactivation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "profile-state.json")

	cfg := profileStateTestConfig(t, path)
	cfg.Hooks.OnStartup.Profile = "coding" // the hook would otherwise reactivate it
	s := profileTestServer(t, cfg, newStubRouter([]string{"real"}, "ok"))

	s.restoreProfileState()
	require.Equal(t, "coding", s.ActiveProfile(), "first boot takes the hook")

	_, err := s.setActiveProfile("")
	require.NoError(t, err)

	s.restoreProfileState()
	assert.Empty(t, s.ActiveProfile(), "turning profiles off must survive a restart")
}

func TestServer_ProfileStateFileFallsBackToHook(t *testing.T) {
	path := filepath.Join(t.TempDir(), "profile-state.json")

	cfg := profileStateTestConfig(t, path)
	cfg.Hooks.OnStartup.Profile = "coding"
	s := profileTestServer(t, cfg, newStubRouter([]string{"real"}, "ok"))
	s.restoreProfileState()

	assert.Equal(t, "coding", s.ActiveProfile())

	data, err := os.ReadFile(path)
	require.NoError(t, err, "the first boot seeds the state file")
	assert.JSONEq(t, `{"active":"coding"}`, string(data))
}

func TestServer_ProfileStateFileIgnoresUnknownProfile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "profile-state.json")
	require.NoError(t, os.WriteFile(path, []byte("{\"active\":\"gone\"}\n"), 0o644))

	cfg := profileStateTestConfig(t, path)
	cfg.Hooks.OnStartup.Profile = "coding"
	s := profileTestServer(t, cfg, newStubRouter([]string{"real"}, "ok"))
	s.restoreProfileState()

	assert.Equal(t, "coding", s.ActiveProfile(), "a stale name is a warning, not a failed start")
}

func TestServer_ProfileStateFileDisabled(t *testing.T) {
	s := profileTestServer(t, profileStateTestConfig(t, ""), newStubRouter([]string{"real"}, "ok"))
	s.restoreProfileState()

	_, err := s.setActiveProfile("coding")
	require.NoError(t, err)
	assert.Equal(t, "coding", s.ActiveProfile(), "an empty path leaves the old behaviour alone")
}
