package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/process"
	"github.com/Wizard815/Argos/internal/router"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func turn(sys, first string, more ...string) string {
	msgs := []map[string]string{{"role": "system", "content": sys}, {"role": "user", "content": first}}
	for i, m := range more {
		role := "assistant"
		if i%2 == 1 {
			role = "user"
		}
		msgs = append(msgs, map[string]string{"role": role, "content": m})
	}
	b, _ := json.Marshal(map[string]any{"model": "par", "messages": msgs})
	return string(b)
}

func TestAffinity_AConversationIsKnownByItsStart(t *testing.T) {
	a := conversationKey([]byte(turn("you help", "fix the bug")))
	b := conversationKey([]byte(turn("you help", "fix the bug", "done", "now the tests")))
	c := conversationKey([]byte(turn("you help", "write docs")))
	assert.NotZero(t, a)
	assert.Equal(t, a, b, "a later turn of the same conversation")
	assert.NotEqual(t, a, c, "another conversation")
	assert.Zero(t, conversationKey([]byte(`{"model":"m"}`)))
}

func affinityServer(t *testing.T) (*Server, *[]int, *sync.Mutex) {
	t.Helper()
	cfg, err := config.LoadConfigFromReader(strings.NewReader(`
models:
  par:
    cmd: llama-server --port ${PORT} -m /x.gguf --parallel 3
  vllm:
    cmd: vllm serve x --port ${PORT} --parallel 3
`))
	require.NoError(t, err)
	stub := newStubRouter([]string{"par", "vllm"}, "ok")
	var mu sync.Mutex
	var slots []int
	stub.serveHTTP = func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var got struct {
			Slot *int `json:"id_slot"`
		}
		json.Unmarshal(body, &got)
		mu.Lock()
		if got.Slot != nil {
			slots = append(slots, *got.Slot)
		} else {
			slots = append(slots, -1)
		}
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}
	s := profileTestServer(t, cfg, stub)
	if s.affinity == nil {
		s.affinity = newAffinity()
	}
	s.affinity.busySlots = func(string) []router.Slot { return nil }
	return s, &slots, &mu
}

func send(s *Server, body string) {
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	s.ServeHTTP(w, req)
}

func TestAffinity_EachConversationKeepsItsSlot(t *testing.T) {
	s, slots, _ := affinityServer(t)
	send(s, turn("sys", "A"))
	send(s, turn("sys", "B"))
	send(s, turn("sys", "A", "ok", "more A"))
	send(s, turn("sys", "C"))
	send(s, turn("sys", "B", "ok", "more B"))
	assert.Equal(t, []int{0, 1, 0, 2, 1}, *slots, "A in 0, B in 1, C in the slot used longest ago")
}

func TestAffinity_ABusySlotIsNeverAskedFor(t *testing.T) {
	s, slots, _ := affinityServer(t)
	send(s, turn("sys", "A")) // A -> slot 0
	s.affinity.busySlots = func(string) []router.Slot { return []router.Slot{{ID: 0, Phase: router.PhaseGenerating}} }
	send(s, turn("sys", "A", "ok", "again"))
	assert.Equal(t, 1, (*slots)[1], "slot 0 is answering someone: A goes to a free slot instead of waiting")
	s.affinity.busySlots = func(string) []router.Slot {
		return []router.Slot{{ID: 0}, {ID: 1}, {ID: 2}}
	}
	send(s, turn("sys", "D"))
	assert.Equal(t, -1, (*slots)[2], "all busy: llama-server chooses")
}

func TestAffinity_OnlyLlamaServerWithSeveralSlotsAndNotWhenTheClientChose(t *testing.T) {
	s, slots, _ := affinityServer(t)
	send(s, strings.Replace(turn("sys", "A"), `"model":"par"`, `"model":"vllm"`, 1))
	send(s, strings.Replace(turn("sys", "A"), `"model":"par"`, `"id_slot":2,"model":"par"`, 1))
	assert.Equal(t, []int{-1, 2}, *slots)
}

func TestAffinity_RequestsAtTheSameMomentGetDifferentSlots(t *testing.T) {
	a := newAffinity()
	a.busySlots = func(string) []router.Slot { return nil } // the log has not shown them yet
	s1, r1 := a.pickSlot("m", 2, 1)
	s2, r2 := a.pickSlot("m", 2, 2)
	s3, _ := a.pickSlot("m", 2, 3)
	assert.Equal(t, []int{0, 1, -1}, []int{s1, s2, s3})
	r1()
	r1() // once only
	s4, _ := a.pickSlot("m", 2, 1)
	assert.Equal(t, 0, s4)
	r2()
}

func TestAffinity_AConversationGoesBackToItsCopy(t *testing.T) {
	cfg := config.Config{Selectors: map[string]config.SelectorConfig{"pool": {
		Strategy: config.SelectorStrategySpillover, Targets: []string{"a", "b"},
		Settings: config.SelectorSettings{Spillover: 2}}},
		Models: map[string]config.ModelConfig{"a": {}, "b": {}}}
	tracker := newSelectorSpilloverTracker(cfg)
	running := map[string]process.ProcessState{"a": process.StateReady, "b": process.StateReady}
	got, err := strategySpillover("pool", tracker, running, "b")
	require.NoError(t, err)
	assert.Equal(t, "b", got, "b has its turns cached and has room")
	strategySpillover("pool", tracker, running, "b")
	got, _ = strategySpillover("pool", tracker, running, "b")
	assert.Equal(t, "a", got, "b is full (2 of 2): it spills over as before")
}
