package server

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

// placingRouter is a stubRouter that also chooses GPUs, like the pool router.
type placingRouter struct {
	*stubRouter
	cards map[string][]string
}

func (p *placingRouter) Placement(id string) []string { return p.cards[id] }

func TestServer_LaunchEnvAppliesPoolPlacement(t *testing.T) {
	local := &placingRouter{stubRouter: &stubRouter{}, cards: map[string][]string{
		"one":  {"1"},
		"both": {"0", "1"},
	}}
	s := newTestServer(local, &stubRouter{})

	got := s.launchEnv("one", nil, []string{"HIP_VISIBLE_DEVICES=0", "FOO=bar"})
	assert.Equal(t, []string{"HIP_VISIBLE_DEVICES=1", "FOO=bar"}, got, "the placement replaces the configured pin")

	got = s.launchEnv("both", nil, []string{"FOO=bar"})
	assert.Equal(t, []string{"FOO=bar", "HIP_VISIBLE_DEVICES=0,1"}, got)
}

func TestServer_LaunchEnvWithoutPlacementIsUnchanged(t *testing.T) {
	env := []string{"HIP_VISIBLE_DEVICES=0"}

	plain := newTestServer(&stubRouter{}, &stubRouter{})
	assert.Equal(t, env, plain.launchEnv("m", nil, env), "a router that cannot place")

	placing := newTestServer(&placingRouter{stubRouter: &stubRouter{}}, &stubRouter{})
	assert.Equal(t, env, placing.launchEnv("m", nil, env), "no placement recorded for the model")
}
