package server

import (
	"bytes"
	"fmt"
	"hash/fnv"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/Wizard815/Argos/internal/chain"
	"github.com/Wizard815/Argos/internal/router"
	"github.com/Wizard815/Argos/internal/swaputil"
	"github.com/tidwall/gjson"
)

// Answer first (routing.router.settings.pool.answerFirst).
//
// llama.cpp serves a model's chats in slots and builds each GPU batch from all
// of them: one token for a chat that is answering, up to a whole n_batch of a
// chat reading its prompt. While one chat reads a long prompt the other's
// answer drops from tens of tokens a second to one or two, because every step
// now takes as long as the prompt chunk. Argos cannot share a step out
// differently or pause a slot once both are inside llama-server; what it can
// do is choose when a request goes in. A long new prompt for a model where
// another chat is still answering waits for that answer to finish (an agent's
// turns leave a gap at each tool call), then goes. Short ones go straight
// through: a few seconds of a slower answer is cheaper than the wait.

const (
	answerFirstPoll     = 250 * time.Millisecond
	answerFirstRemember = 16 // recent requests per model, to find a conversation's new part
	bytesPerToken       = 4
)

type answerFirstState struct {
	mu     sync.Mutex
	recent map[string][][]uint64 // model -> message hashes of its recent requests, newest last
	slots  func(model string) []router.Slot
	poll   time.Duration
}

func newAnswerFirst() *answerFirstState {
	return &answerFirstState{recent: map[string][][]uint64{}, slots: router.Prefills.Slots, poll: answerFirstPoll}
}

// newTokens estimates how much of a request's prompt the model has not read
// before: the messages after the longest run it shares with a recent request
// to the same model (a conversation's earlier turns sit in the slot's cache),
// or all of it, tool definitions included, for a conversation it has not
// seen. It remembers the request for the next one.
func (a *answerFirstState) newTokens(model string, body []byte) int {
	msgs := gjson.GetBytes(body, "messages")
	if !msgs.IsArray() {
		return len(gjson.GetBytes(body, "prompt").Raw) / bytesPerToken
	}
	var hashes []uint64
	var sizes []int
	msgs.ForEach(func(_, m gjson.Result) bool {
		h := fnv.New64a()
		h.Write([]byte(m.Raw))
		hashes = append(hashes, h.Sum64())
		sizes = append(sizes, len(m.Raw))
		return true
	})

	a.mu.Lock()
	shared := 0
	for _, prev := range a.recent[model] {
		n := 0
		for n < len(prev) && n < len(hashes) && prev[n] == hashes[n] {
			n++
		}
		shared = max(shared, n)
	}
	list := append(a.recent[model], hashes)
	if len(list) > answerFirstRemember {
		list = list[len(list)-answerFirstRemember:]
	}
	a.recent[model] = list
	a.mu.Unlock()

	newBytes := 0
	for _, s := range sizes[shared:] {
		newBytes += s
	}
	if shared == 0 {
		newBytes += len(gjson.GetBytes(body, "tools").Raw)
	}
	return newBytes / bytesPerToken
}

// answering reports whether a chat on the model is generating right now.
func (a *answerFirstState) answering(model string) bool {
	for _, s := range a.slots(model) {
		if s.Phase == router.PhaseGenerating {
			return true
		}
	}
	return false
}

// CreateAnswerFirstMiddleware holds a long new prompt while another chat on
// the same model is answering. It runs after the in-flight tracker, so a held
// request is listed as running and marked held.
func CreateAnswerFirstMiddleware(s *Server) chain.Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			af := s.cfg.Routing.Router.Settings.Pool.AnswerFirst
			data, ok := swaputil.ReadContext(r.Context())
			if !ok || s.answerFirst == nil || r.Method != http.MethodPost || len(af.Models) == 0 || !af.Applies(data.ModelID) {
				next.ServeHTTP(w, r)
				return
			}
			body, err := io.ReadAll(r.Body)
			r.Body = io.NopCloser(bytes.NewReader(body))
			if err != nil {
				next.ServeHTTP(w, r)
				return
			}
			tokens := s.answerFirst.newTokens(data.ModelID, body)
			if tokens < af.MinPromptTokens || !s.answerFirst.answering(data.ModelID) {
				next.ServeHTTP(w, r)
				return
			}

			iw, _ := w.(*inflightResponseWriter)
			if iw != nil {
				iw.tracker.SetHeld(iw.id, fmt.Sprintf("answer first: a ~%d-token prompt waits while another chat on this model is answering", tokens))
			}
			start := time.Now()
			deadline := start.Add(time.Duration(af.MaxWaitSeconds) * time.Second)
			tick := time.NewTicker(s.answerFirst.poll)
		wait:
			for s.answerFirst.answering(data.ModelID) {
				select {
				case <-r.Context().Done():
					tick.Stop()
					return // the client gave up while it waited
				case <-tick.C:
					if time.Now().After(deadline) {
						break wait
					}
				}
			}
			tick.Stop()
			if iw != nil {
				iw.tracker.SetHeld(iw.id, "")
			}
			s.proxylog.Infof("<%s> answer first: held a ~%d-token prompt for %s while another chat answered",
				data.ModelID, tokens, time.Since(start).Round(100*time.Millisecond))
			next.ServeHTTP(w, r)
		})
	}
}
