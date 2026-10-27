package router

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// The model from the logs: it loads, its first request hits an assert, it
// exits, the next request starts it again. The third crash in ten minutes
// stops requests from starting it.
func TestCrash_RemembersTheLineAndStopsAReloadLoop(t *testing.T) {
	now := time.Unix(1000, 0)
	forgot := []string{}
	c := newCrashWatch(func() time.Time { return now }, func(m string) { forgot = append(forgot, m) })

	crash := func() {
		c.State("m", "stopped", "starting")
		c.line("m", []byte("0.31.430.420 I srv  llama_server: model loaded"))
		c.line("m", []byte("/src/ggml/src/ggml-cuda/common.cuh:1262: GGML_ASSERT(ptr == nullptr) failed"))
		c.line("m", []byte("/src/build/bin/libggml-base.so.0(ggml_abort+0x15b)[0x146eb6008abb]"))
		c.State("m", "starting", "ready")
		c.State("m", "ready", "stopped")
		now = now.Add(time.Minute)
	}

	crash()
	cr, ok := c.Get("m")
	require.True(t, ok)
	assert.Equal(t, "/src/ggml/src/ggml-cuda/common.cuh:1262: GGML_ASSERT(ptr == nullptr) failed", cr.Line, "the first fatal line")
	assert.Equal(t, 1, cr.Recent)
	_, blocked := c.Blocked("m")
	assert.False(t, blocked)
	assert.Equal(t, []string{"m"}, forgot, "a crashed process has no busy slots")

	crash()
	crash()
	cr, blocked = c.Blocked("m")
	assert.True(t, blocked, "three crashes in ten minutes")
	assert.Equal(t, 3, cr.Recent)

	c.Clear("m")
	_, blocked = c.Blocked("m")
	assert.False(t, blocked, "loading it by hand clears it")
}

func TestCrash_AStopTheEngineAskedForIsNotACrash(t *testing.T) {
	c := newCrashWatch(time.Now, nil)
	c.State("m", "stopped", "starting")
	c.State("m", "starting", "ready")
	c.State("m", "ready", "stopping")
	c.State("m", "stopping", "stopped")
	_, ok := c.Get("m")
	assert.False(t, ok)
}

func TestCrash_OldCrashesExpire(t *testing.T) {
	now := time.Unix(1000, 0)
	c := newCrashWatch(func() time.Time { return now }, nil)
	for i := 0; i < 3; i++ {
		if i > 0 {
			now = now.Add(6 * time.Minute)
		}
		c.State("m", "starting", "stopped")
	}
	cr, ok := c.Get("m")
	require.True(t, ok)
	assert.Equal(t, 2, cr.Recent, "the first was twelve minutes before the third")
	assert.Equal(t, "exited while starting, without an error line (check its log)", cr.Line)
	assert.False(t, cr.Blocked, "three crashes over twelve minutes are not three in ten")
	now = now.Add(11 * time.Minute)
	_, ok = c.Get("m")
	assert.False(t, ok, "nothing in the last ten minutes")
}
