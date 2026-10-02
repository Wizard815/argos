package server

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func writeFileT(t *testing.T, path, value string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(value+"\n"), 0o644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

// The box's kernel exposes only total/used, so free has to be derived. Before
// this fallback the read returned nothing, the copies were never generated and
// multi_model silently did nothing.
func TestServer_ReadFreeVRAMFallsBackToTotalMinusUsed(t *testing.T) {
	root := t.TempDir()

	dev0 := filepath.Join(root, "card0", "device")
	require.NoError(t, os.MkdirAll(dev0, 0o755))
	writeFileT(t, filepath.Join(dev0, "mem_info_vram_total"), strconv.FormatInt(32<<30, 10))
	writeFileT(t, filepath.Join(dev0, "mem_info_vram_used"), strconv.FormatInt(2<<30, 10))

	writeCard(t, root, "1", 20) // this one exposes free directly

	free := readFreeVRAMGiB(root)
	assert.Equal(t, 30, free["0"], "want total - used when free is absent")
	assert.Equal(t, 20, free["1"], "want free when it is present")
}

// With memory unreadable the cards are still there, and the copies still have to
// be generated.
func TestServer_ListDRMCardsFallsBackToCardNodes(t *testing.T) {
	root := t.TempDir()
	require.NoError(t, os.MkdirAll(filepath.Join(root, "card0"), 0o755))
	require.NoError(t, os.MkdirAll(filepath.Join(root, "card1"), 0o755))

	assert.Equal(t, []string{"0", "1"}, listDRMCards(root))
}

// A pin rewrites the model id before selectors resolve, which would bypass the
// selector entirely, so multi_model must decline its pin.
func TestServer_ProfilePinDeclinedForMultiModel(t *testing.T) {
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
models:
  base:
    cmd: llama-server --port ${PORT} -m /x.gguf
  base--var:
    cmd: llama-server --port ${PORT} -m /x.gguf
profiles:
  p:
    pins:
      base: base--var
`))
	require.NoError(t, err)
	s := profileTestServer(t, cfg, newStubRouter([]string{"base"}, "ok"))
	_, err = s.setActiveProfile("p")
	require.NoError(t, err)

	target, pinned := s.profilePin("base")
	require.True(t, pinned, "a plain model honours its pin")
	assert.Equal(t, "base--var", target)

	s.modelModesMu.Lock()
	s.modelModes = map[string]string{"base": ModeMultiModel}
	s.modelModesMu.Unlock()

	_, pinned = s.profilePin("base")
	assert.False(t, pinned, "multi_model must not be rewritten by a pin")
}
