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

// Two chats on one model are two slots: one reading its prompt while the other
// generates, each followed on its own (the log of a model run with -np 2).
func TestPrefill_FollowsEachSlotOfAModel(t *testing.T) {
	now := time.Unix(1000, 0)
	w := NewPrefillWatch(func() time.Time { return now })
	feed := func(lines ...string) {
		for _, l := range lines {
			w.Feed("m", []byte(l+"\n\n"))
		}
	}
	feed("0.18.245.813 I slot get_availabl: id  1 | task -1 | selected slot by LRU, t_last = -1",
		"0.18.320.326 I slot launch_slot_: id  1 | task 0 | processing task, is_child = 0",
		"2.14.056.695 I slot print_timing: id  1 | task 0 | prompt processing, n_tokens =  64895, progress = 0.98, t = 115.33 s / 562.70 tokens per second",
		"2.17.223.661 I slot print_timing: id  1 | task 0 | prompt processing, n_tokens =  65973, progress = 1.00, t = 118.87 s / 555.02 tokens per second",
		"2.20.520.421 I slot print_timing: id  1 | task 0 | n_gen =    132, tg =  43.21 t/s, tg_3s =  43.53 t/s")
	now = now.Add(time.Second)
	feed("2.55.409.563 I slot launch_slot_: id  0 | task 775 | processing task, is_child = 0",
		"3.00.137.631 I slot print_timing: id  0 | task 775 | prompt processing, n_tokens =   4088, progress = 0.16, t =   4.49 s / 909.70 tokens per second",
		"3.02.567.282 I slot print_timing: id  1 | task 0 | n_gen =   1586, tg =  35.16 t/s, tg_3s =   0.82 t/s")

	slots := w.Slots("m")
	require.Len(t, slots, 2)
	gen, pre := slots[0], slots[1]
	assert.Equal(t, 1, gen.ID, "oldest request first")
	assert.Equal(t, PhaseGenerating, gen.Phase)
	assert.Equal(t, 1586, gen.Generated)
	assert.InDelta(t, 0.82, gen.GenTokensPerSecond, 1e-9, "the speed of the last few seconds, not the average")
	assert.Equal(t, 0, pre.ID)
	assert.Equal(t, PhasePrefill, pre.Phase)
	assert.Equal(t, 4088, pre.Tokens)
	assert.Equal(t, 25550, pre.Total)

	pf, ok := w.Get("m")
	require.True(t, ok)
	assert.Equal(t, 4088, pf.Tokens, "the model's prefill is the slot reading a prompt, not the one generating")

	feed("4.00.000.000 I slot      release: id  1 | task 0 | stop processing: n_tokens = 67600, truncated = 0")
	slots = w.Slots("m")
	require.Len(t, slots, 1)
	assert.Equal(t, 0, slots[0].ID)

	w.Forget("m")
	assert.Empty(t, w.Slots("m"), "a stopped process has no busy slot")
}
