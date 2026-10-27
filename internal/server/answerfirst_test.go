package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/router"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func chatBody(model string, msgs ...string) string {
	parts := make([]string, len(msgs))
	for i, m := range msgs {
		parts[i] = fmt.Sprintf(`{"role":"user","content":%q}`, m)
	}
	return fmt.Sprintf(`{"model":%q,"messages":[%s]}`, model, strings.Join(parts, ","))
}

// A conversation's earlier turns are in its slot's cache: only what is new
// counts. A chat the model has not seen counts whole, tools included.
func TestAnswerFirst_CountsOnlyTheNewPartOfAConversation(t *testing.T) {
	a := newAnswerFirst()
	long := strings.Repeat("x", 40000)
	first := a.newTokens("m", []byte(`{"tools":[{"name":"`+strings.Repeat("t", 4000)+`"}],`+chatBody("m", long)[1:]))
	assert.Greater(t, first, 10000, "a new chat: the whole prompt and its tools")
	next := a.newTokens("m", []byte(chatBody("m", long, "and now this")))
	assert.Less(t, next, 20, "the next turn: only the new message")
	other := a.newTokens("m", []byte(chatBody("m", strings.Repeat("y", 40000))))
	assert.Greater(t, other, 9000, "another chat: nothing shared")
}

func answerFirstServer(t *testing.T, answering *atomic.Bool) (*Server, *[]string, *sync.Mutex) {
	t.Helper()
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
routing:
  router:
    use: pool
    settings:
      pool:
        answerFirst:
          models: [m]
          minPromptTokens: 1000
          maxWaitSeconds: 5
models:
  m:
    cmd: llama-server --port ${PORT} -m /x.gguf
  other:
    cmd: llama-server --port ${PORT} -m /y.gguf
`))
	require.NoError(t, err)
	stub := newStubRouter([]string{"m", "other"}, "ok")
	var mu sync.Mutex
	var reached []string
	stub.serveHTTP = func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		reached = append(reached, string(body[:min(len(body), 40)]))
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}
	s := profileTestServer(t, cfg, stub)
	s.answerFirst.poll = 10 * time.Millisecond
	s.answerFirst.slots = func(model string) []router.Slot {
		if model == "m" && answering.Load() {
			return []router.Slot{{ID: 0, Phase: router.PhaseGenerating}}
		}
		return nil
	}
	return s, &reached, &mu
}

func post(s *Server, body string) int {
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	s.ServeHTTP(w, req)
	return w.Code
}

func TestAnswerFirst_ALongPromptWaitsWhileAnotherChatAnswers(t *testing.T) {
	var answering atomic.Bool
	answering.Store(true)
	s, reached, mu := answerFirstServer(t, &answering)

	// a short prompt and another model's long prompt are not held
	assert.Equal(t, http.StatusOK, post(s, chatBody("m", "hi")))
	assert.Equal(t, http.StatusOK, post(s, chatBody("other", strings.Repeat("z", 20000))))

	done := make(chan int, 1)
	go func() { done <- post(s, chatBody("m", strings.Repeat("q", 20000))) }()
	time.Sleep(150 * time.Millisecond)
	select {
	case <-done:
		t.Fatal("a long prompt went in while another chat was answering")
	default:
	}
	held := s.inflight.Current().Requests
	require.Len(t, held, 1)
	assert.Contains(t, held[0].Metadata["held"], "answer first", "listed as running, marked held")

	answering.Store(false) // the other chat finished its answer
	select {
	case code := <-done:
		assert.Equal(t, http.StatusOK, code)
	case <-time.After(2 * time.Second):
		t.Fatal("the held prompt did not go once the answer finished")
	}
	mu.Lock()
	assert.Len(t, *reached, 3)
	mu.Unlock()
}

func TestAnswerFirst_AHeldPromptGoesAfterTheLongestWait(t *testing.T) {
	var answering atomic.Bool
	answering.Store(true)
	s, _, _ := answerFirstServer(t, &answering)
	s.cfg.Routing.Router.Settings.Pool.AnswerFirst.MaxWaitSeconds = 0 // already past it on the first look
	start := time.Now()
	assert.Equal(t, http.StatusOK, post(s, chatBody("m", strings.Repeat("q", 20000))))
	assert.Less(t, time.Since(start), time.Second)
}
