package server

import (
	"strconv"
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func multiModelTestConfig(t *testing.T) config.Config {
	t.Helper()
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
models:
  scaled:
    cmd: llama-server --port ${PORT} --parallel 2 -m /scaled.gguf
    env:
      - HIP_VISIBLE_DEVICES=0,1
  plain:
    cmd: llama-server --port ${PORT} -m /plain.gguf
`))
	require.NoError(t, err)
	return cfg
}

func TestServer_ApplyMultiModelExpandsCopiesAndSelector(t *testing.T) {
	cfg := multiModelTestConfig(t)
	modes := map[string]string{"scaled": ModeMultiModel}

	applyMultiModel(&cfg, modes, nil, nil, []string{"0", "1"})

	_, stillAModel := cfg.Models["scaled"]
	assert.True(t, stillAModel,
		"the base stays a model so the UI can still list it and change its mode")

	sel, isSelector := cfg.Selectors["scaled"]
	require.True(t, isSelector, "the base id should become a selector")
	assert.Equal(t, config.SelectorStrategySpillover, sel.Strategy)
	assert.Equal(t, []string{"scaled--mm0", "scaled--mm1"}, sel.Targets)
	assert.Equal(t, 2, sel.Settings.Spillover, "spillover is the model's --parallel")

	for i, card := range []string{"0", "1"} {
		id := sel.Targets[i]
		cp, ok := cfg.Models[id]
		require.True(t, ok, "copy %s should exist", id)
		assert.True(t, cp.Unlisted, "copies should not clutter the model list")
		assert.Equal(t, card, envValue(cp.Env, "HIP_VISIBLE_DEVICES"))
		_, hasMode := modes[id]
		assert.False(t, hasMode,
			"copies stay off single_gpu so their own per-card pin is respected")
	}

	g, ok := cfg.Routing.Router.Settings.Groups[multiModelGroupID]
	require.True(t, ok, "copies need a group")
	assert.False(t, g.Swap, "copies must coexist")
	assert.False(t, g.Exclusive)
	assert.ElementsMatch(t, sel.Targets, g.Members)
}

func TestServer_ApplyMultiModelLeavesOtherModesAlone(t *testing.T) {
	cfg := multiModelTestConfig(t)
	modes := map[string]string{"plain": ModeSingleGPU}

	applyMultiModel(&cfg, modes, nil, nil, []string{"0", "1"})

	_, stillAModel := cfg.Models["plain"]
	assert.True(t, stillAModel, "a model without multi_model must be untouched")
	_, isSelector := cfg.Selectors["plain"]
	assert.False(t, isSelector)
	assert.Equal(t, ModeSingleGPU, modes["plain"])
}

func TestServer_ApplyMultiModelWithoutCardsIsNoop(t *testing.T) {
	cfg := multiModelTestConfig(t)
	modes := map[string]string{"scaled": ModeMultiModel}

	applyMultiModel(&cfg, modes, nil, nil, nil)

	_, stillAModel := cfg.Models["scaled"]
	assert.True(t, stillAModel, "with no cards there is nothing to spread across")
}

func TestServer_ParallelFromCmd(t *testing.T) {
	assert.Equal(t, 2, parallelFromCmd("llama-server --parallel 2 -m x"))
	assert.Equal(t, 3, parallelFromCmd("llama-server --parallel=3 -m x"))
	assert.Equal(t, 1, parallelFromCmd("llama-server -m x"))
	assert.Equal(t, 4, parallelFromCmd("srv -np 4 -m x"))
	assert.Equal(t, 1, parallelFromCmd(""))
}

// Ports are allocated at load, before the expansion, so without retargeting
// every copy inherits the base's port and the second one cannot bind it — which
// is exactly what happened on the box ("couldn't bind HTTP server socket ...
// port: 5825").
func TestServer_ApplyMultiModelGivesEachCopyItsOwnPort(t *testing.T) {
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
startPort: 5800
models:
  scaled:
    cmd: llama-server --port ${PORT} -m /x.gguf
    proxy: http://localhost:${PORT}
`))
	require.NoError(t, err)

	basePort := modelPort(cfg.Models["scaled"])
	require.NotZero(t, basePort, "the loader substitutes ${PORT} at load")

	modes := map[string]string{"scaled": ModeMultiModel}
	applyMultiModel(&cfg, modes, nil, nil, []string{"0", "1"})

	p0 := modelPort(cfg.Models["scaled--mm0"])
	p1 := modelPort(cfg.Models["scaled--mm1"])
	assert.NotEqual(t, basePort, p0, "a copy must move off the base's port")
	assert.NotEqual(t, basePort, p1)
	assert.NotEqual(t, p0, p1, "each copy needs its own port")
	assert.Contains(t, cfg.Models["scaled--mm0"].Cmd, strconv.Itoa(p0))
	assert.Equal(t, "http://localhost:"+strconv.Itoa(p0), cfg.Models["scaled--mm0"].Proxy)
}
