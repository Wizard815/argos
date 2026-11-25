package server

import (
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/router"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func autoServer(t *testing.T, extra string, answering *atomic.Bool) *Server {
	t.Helper()
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
routing:
  router:
    use: pool
    settings:
      pool:
        answerFirst:
          autoMaxWaitSeconds: 5
` + extra + `
models:
  par:
    cmd: llama-server --port ${PORT} -m /x.gguf --parallel 2
  solo:
    cmd: llama-server --port ${PORT} -m /y.gguf
`))
	require.NoError(t, err)
	stub := newStubRouter([]string{"par", "solo"}, "ok")
	stub.serveHTTP = func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }
	s := profileTestServer(t, cfg, stub)
	s.answerFirst.poll = 10 * time.Millisecond
	s.answerFirst.slots = func(model string) []router.Slot {
		if answering.Load() {
			return []router.Slot{{ID: 0, Phase: router.PhaseGenerating}}
		}
		return nil
	}
	return s
}

// held reports whether a long prompt for model waits while a chat answers.
func held(s *Server, model string, answering *atomic.Bool, prompt string) bool {
	done := make(chan int, 1)
	go func() { done <- post(s, chatBody(model, prompt)) }()
	select {
	case <-done:
		return false
	case <-time.After(150 * time.Millisecond):
	}
	answering.Store(false)
	<-done
	answering.Store(true)
	return true
}

func TestAnswerFirst_AutoCoversEveryParallelModel(t *testing.T) {
	var answering atomic.Bool
	answering.Store(true)
	s := autoServer(t, "", &answering)
	long := strings.Repeat("q", 20000) // ~5000 tokens
	assert.True(t, held(s, "par", &answering, long), "a --parallel 2 model is covered without being listed")
	assert.False(t, held(s, "solo", &answering, long), "one chat at a time: nothing to protect")
	assert.False(t, held(s, "par", &answering, "short question"), "a short prompt goes straight in")

	off := autoServer(t, "          auto: false", &answering)
	assert.False(t, held(off, "par", &answering, long), "auto: false covers only the listed models")
}

func TestAnswerFirst_MeasuredSpeedDecides(t *testing.T) {
	var answering atomic.Bool
	answering.Store(true)
	s := autoServer(t, "          stallSeconds: 2", &answering)
	defer func(f func(string) (float64, bool)) { prefillSpeed = f }(prefillSpeed)
	long := strings.Repeat("q", 20000) // ~5000 tokens

	// every prompt a new conversation: a repeat would be cached, nothing new to read
	prefillSpeed = func(string) (float64, bool) { return 10000, true } // 0.5 s to read: not worth a wait
	assert.False(t, held(s, "par", &answering, long))
	prefillSpeed = func(string) (float64, bool) { return 500, true } // 10 s of slow answers: wait
	assert.True(t, held(s, "par", &answering, strings.Repeat("w", 20000)))
	prefillSpeed = func(string) (float64, bool) { return 500, true }
	assert.False(t, held(s, "par", &answering, strings.Repeat("q", 2000)), "500 tokens at 500 t/s: 1 s, under the 2 s")
}

func TestAnswerFirst_AutoWaitsLessThanListed(t *testing.T) {
	var answering atomic.Bool
	answering.Store(true)
	s := autoServer(t, "", &answering)
	s.cfg.Routing.Router.Settings.Pool.AnswerFirst.AutoMaxWaitSeconds = 1
	start := time.Now()
	assert.Equal(t, http.StatusOK, post(s, chatBody("par", strings.Repeat("q", 20000))))
	assert.InDelta(t, 1.0, time.Since(start).Seconds(), 0.5, "goes after autoMaxWaitSeconds even while the other chat answers")
}

func TestPrefillWatch_SpeedIsTheMedianOfRecentPrompts(t *testing.T) {
	now := time.Unix(1000, 0)
	w := router.NewPrefillWatch(func() time.Time { return now })
	_, ok := w.Speed("m")
	assert.False(t, ok)
	feed := func(task int, tokens int, secs float64) {
		w.Feed("m", []byte("slot launch_slot_: id  0 | task "+itoa(task)+" | processing task, is_child = 0\n"))
		now = now.Add(time.Duration(secs * float64(time.Second)))
		w.Feed("m", []byte("slot print_timing: id  0 | task "+itoa(task)+" | prompt processing, n_tokens = "+itoa(tokens)+
			", progress = 1.00, t = "+strconv.FormatFloat(secs, 'f', 2, 64)+" s / 1.00 tokens per second\n"))
		w.Feed("m", []byte("slot update_slots: id  0 | task "+itoa(task)+" | prompt done, n_tokens = "+itoa(tokens)+", batch.n_tokens = 1\n"))
		w.Feed("m", []byte("slot      release: id  0 | task "+itoa(task)+" | stop processing: n_tokens = "+itoa(tokens)+", truncated = 0\n"))
	}
	feed(1, 4000, 4)  // 1000 t/s
	feed(2, 6000, 3)  // 2000 t/s
	feed(3, 100, 0.1) // too short to time
	feed(4, 3000, 2)  // 1500 t/s
	sp, ok := w.Speed("m")
	require.True(t, ok)
	assert.InDelta(t, 1500, sp, 1)
}

func itoa(n int) string { return strconv.Itoa(n) }
