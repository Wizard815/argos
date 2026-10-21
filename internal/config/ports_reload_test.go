package config

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func writeModels(t *testing.T, path string, ids ...string) {
	t.Helper()
	body := "models:\n"
	for _, id := range ids {
		body += "  " + id + ":\n    cmd: llama-server --port ${PORT} -m /m/" + id + ".gguf\n"
	}
	require.NoError(t, os.WriteFile(path, []byte(body), 0o644))
}

// A model added to the folder sorts before a running one. Numbering by model
// id moved the running model's port, so its config "changed" and the reload
// stopped it. With the previous ports it keeps its own.
func TestConfig_ReloadKeepsPortsWhenAModelIsAdded(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	writeModels(t, path, "qwen-b")
	before, err := LoadConfigSources(path, "")
	require.NoError(t, err)
	require.Equal(t, 5800, before.Ports["qwen-b"])

	writeModels(t, path, "qwen-a", "qwen-b")
	fresh, err := LoadConfigSources(path, "")
	require.NoError(t, err)
	assert.Equal(t, 5801, fresh.Ports["qwen-b"], "without hints the old order-based numbering is unchanged")

	after, err := LoadConfigSourcesKeepingPorts(path, "", before.Ports)
	require.NoError(t, err)
	assert.Equal(t, 5800, after.Ports["qwen-b"])
	assert.Equal(t, 5801, after.Ports["qwen-a"], "the new model takes the next free port")
	assert.True(t, ModelConfigEqual(before.Models["qwen-b"], after.Models["qwen-b"]))
}

func TestConfig_ReloadPortHintsNeverCollide(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	writeModels(t, path, "a", "b")
	cfg, err := LoadConfigSourcesKeepingPorts(path, "", map[string]int{"a": 5801, "b": 5801})
	require.NoError(t, err)
	assert.NotEqual(t, cfg.Ports["a"], cfg.Ports["b"])
}
