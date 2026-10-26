package router

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// The lines a llama-server prints for one long prompt, as the engine logged them.
func TestPrefill_FollowsAPromptThroughTheLog(t *testing.T) {
	now := time.Unix(1000, 0)
	w := NewPrefillWatch(func() time.Time { return now })
	feed := func(s string) { w.Feed("m", []byte(s)) }

	_, ok := w.Get("m")
	assert.False(t, ok, "nothing until a task starts")

	feed("1.30.554.665 I slot launch_slot_: id  0 | task 0 | processing task, is_child = 0\n")
	pf, ok := w.Get("m")
	require.True(t, ok, "a task that started is prefilling")
	assert.Zero(t, pf.Progress)

	// a progress line split across two writes
	feed("1.52.283.965 I slot print_timing: id  0 | task 0 | prompt processing, n_tokens =  10240, ")
	feed("progress = 0.53, t =  21.73 s / 471.25 tokens per second\n")
	pf, ok = w.Get("m")
	require.True(t, ok)
	assert.Equal(t, 10240, pf.Tokens)
	assert.InDelta(t, 0.53, pf.Progress, 1e-9)
	assert.InDelta(t, 471.25, pf.TokensPerSecond, 1e-9)
	assert.Equal(t, 19321, pf.Total, "the prompt's length, from tokens and progress")

	feed("2.25.000.000 I slot update_slots: id  0 | task 0 | prompt done, n_tokens = 19358, batch.n_tokens = 1\n")
	_, ok = w.Get("m")
	assert.False(t, ok, "generating is not prefilling")
}

func TestPrefill_EndsOnReleaseAndGoesStale(t *testing.T) {
	now := time.Unix(1000, 0)
	w := NewPrefillWatch(func() time.Time { return now })
	w.Feed("m", []byte("slot launch_slot_: id 0 | task 3 | processing task\n"))
	w.Feed("m", []byte("slot release: id 0 | task 3 | stop processing: n_tokens = 20, truncated = 0\n"))
	_, ok := w.Get("m")
	assert.False(t, ok, "a released slot is done")

	w.Feed("m", []byte("slot launch_slot_: id 0 | task 4 | processing task\n"))
	now = now.Add(3 * time.Minute)
	_, ok = w.Get("m")
	assert.False(t, ok, "a model that went quiet mid-prompt (crashed, killed) is not shown prefilling forever")
}

func TestPrefill_TapPassesTheLogOn(t *testing.T) {
	w := NewPrefillWatch(time.Now)
	var out []byte
	tap := w.Tap("m", writerFunc(func(b []byte) (int, error) { out = append(out, b...); return len(b), nil }))
	_, err := tap.Write([]byte("slot launch_slot_: id 0 | task 0 | processing task\n"))
	require.NoError(t, err)
	assert.Equal(t, "slot launch_slot_: id 0 | task 0 | processing task\n", string(out))
	_, ok := w.Get("m")
	assert.True(t, ok)
}

type writerFunc func([]byte) (int, error)

func (f writerFunc) Write(b []byte) (int, error) { return f(b) }
