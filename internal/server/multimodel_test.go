package server

import (
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

	applyMultiModel(&cfg, modes, []string{"0", "1"})

	_, stillAModel := cfg.Models["scaled"]
	assert.False(t, stillAModel, "the base id should stop being a model")

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
		assert.Equal(t, ModeSingleGPU, modes[id],
			"copies run as single_gpu so the launch policy can move them")
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

	applyMultiModel(&cfg, modes, []string{"0", "1"})

	_, stillAModel := cfg.Models["plain"]
	assert.True(t, stillAModel, "a model without multi_model must be untouched")
	_, isSelector := cfg.Selectors["plain"]
	assert.False(t, isSelector)
	assert.Equal(t, ModeSingleGPU, modes["plain"])
}

func TestServer_ApplyMultiModelWithoutCardsIsNoop(t *testing.T) {
	cfg := multiModelTestConfig(t)
	modes := map[string]string{"scaled": ModeMultiModel}

	applyMultiModel(&cfg, modes, nil)

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
