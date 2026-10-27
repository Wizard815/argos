package server

import (
	"context"
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

// One at a time: the model gets one request, the rest wait in order; Go next
// moves a waiting one to the front; one whose client leaves gives up its turn.
func TestSlotGate_OneAtATimeInOrderWithGoNext(t *testing.T) {
	held := map[string]string{}
	var mu sync.Mutex
	g := newSlotGate(func(id, why string) { mu.Lock(); held[id] = why; mu.Unlock() })
	ctx := context.Background()
	require.True(t, g.acquire(ctx, "m", "a"), "a free model takes the first at once")

	order := make(chan string, 3)
	run := func(id string) {
		go func() {
			if g.acquire(ctx, "m", id) {
				order <- id
				g.release("m")
			}
		}()
	}
	run("b")
	time.Sleep(20 * time.Millisecond)
	run("c")
	time.Sleep(20 * time.Millisecond)
	gone, leave := context.WithCancel(ctx)
	left := make(chan bool, 1)
	go func() { left <- g.acquire(gone, "m", "d") }()
	time.Sleep(20 * time.Millisecond)

	mu.Lock()
	assert.Contains(t, held["c"], "2 ahead")
	mu.Unlock()
	assert.True(t, g.next("c"), "Go next")
	mu.Lock()
	assert.Contains(t, held["c"], "1 ahead", "c is first in line now")
	mu.Unlock()
	leave()
	assert.False(t, <-left, "d's client left while it waited")

	g.release("m") // a is done
	assert.Equal(t, "c", <-order)
	assert.Equal(t, "b", <-order)
	assert.False(t, g.next("zzz"), "not waiting")
	require.True(t, g.acquire(ctx, "m", "e"), "free again once everyone is done")
}

func TestAnswerFirst_GoNextEndsAHold(t *testing.T) {
	var answering atomic.Bool
	answering.Store(true)
	s, _, _ := answerFirstServer(t, &answering)
	done := make(chan int, 1)
	go func() { done <- post(s, chatBody("m", strings.Repeat("q", 20000))) }()
	time.Sleep(100 * time.Millisecond)
	reqs := s.inflight.Current().Requests
	require.Len(t, reqs, 1)
	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/api/inflight/"+reqs[0].ID+"/next", nil))
	require.Equal(t, http.StatusOK, w.Code, w.Body.String())
	select {
	case code := <-done:
		assert.Equal(t, http.StatusOK, code)
	case <-time.After(2 * time.Second):
		t.Fatal("Go next did not send the held prompt on")
	}
}

func TestOneAtATime_TheSecondChatWaitsForTheFirst(t *testing.T) {
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
routing:
  router:
    use: pool
    settings:
      pool:
        oneAtATime: [m]
models:
  m:
    cmd: llama-server --port ${PORT} -m /x.gguf
`))
	require.NoError(t, err)
	stub := newStubRouter([]string{"m"}, "ok")
	release := make(chan struct{})
	var inside atomic.Int32
	var most atomic.Int32
	stub.serveHTTP = func(w http.ResponseWriter, r *http.Request) {
		n := inside.Add(1)
		if n > most.Load() {
			most.Store(n)
		}
		<-release
		inside.Add(-1)
		w.WriteHeader(http.StatusOK)
	}
	s := profileTestServer(t, cfg, stub)
	codes := make(chan int, 2)
	go func() { codes <- post(s, chatBody("m", "one")) }()
	go func() { codes <- post(s, chatBody("m", "two")) }()
	time.Sleep(150 * time.Millisecond)
	assert.Equal(t, int32(1), inside.Load(), "only one request is in the model")
	close(release)
	assert.Equal(t, http.StatusOK, <-codes)
	assert.Equal(t, http.StatusOK, <-codes)
	assert.Equal(t, int32(1), most.Load())
}
