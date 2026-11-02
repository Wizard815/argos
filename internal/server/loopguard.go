package server

import (
	"encoding/json"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/Wizard815/Argos/internal/chain"
	"github.com/Wizard815/Argos/internal/swaputil"
)

// loopGuard stops a client's requests to one model: what Schedule -> Live's
// loop guard asks for when a client keeps sending the same request (an agent
// stuck repeating a tool call). The builder finds the loop in the request
// history; the engine refuses the client's next requests for that model at
// once with 429 and says why, which also ends most agent loops, until a person
// lets it run again. Blocks live in memory: a restart clears them.
type loopGuard struct {
	mu     sync.RWMutex
	blocks map[string]loopBlock // src + "\x00" + model
}

type loopBlock struct {
	Src     string    `json:"src"`
	Model   string    `json:"model"`
	Reason  string    `json:"reason"`
	Since   time.Time `json:"since"`
	Refused int       `json:"refused"`
}

func newLoopGuard() *loopGuard { return &loopGuard{blocks: map[string]loopBlock{}} }

func guardKey(src, model string) string { return src + "\x00" + model }

// blocked reports the block for this client and model, counting the refusal.
func (g *loopGuard) blocked(src, model string) (loopBlock, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	b, ok := g.blocks[guardKey(src, model)]
	if ok {
		b.Refused++
		g.blocks[guardKey(src, model)] = b
	}
	return b, ok
}

func (g *loopGuard) list() []loopBlock {
	g.mu.RLock()
	defer g.mu.RUnlock()
	out := make([]loopBlock, 0, len(g.blocks))
	for _, b := range g.blocks {
		out = append(out, b)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Since.Before(out[j].Since) })
	return out
}

// CreateLoopGuardMiddleware refuses the requests of a client stopped for a
// model. It runs after the model is resolved, before the request is tracked.
func CreateLoopGuardMiddleware(s *Server) chain.Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if data, ok := swaputil.ReadContext(r.Context()); ok && data.ModelID != "" {
				if b, stop := s.guard.blocked(clientIP(r), data.ModelID); stop {
					w.Header().Set("Retry-After", "60")
					swaputil.SendResponse(w, r, http.StatusTooManyRequests,
						"Argos stopped this client's requests to "+b.Model+": "+b.Reason+
							". It looks like a loop. Let it run again on Argos -> Schedule -> Live -> Loop guard.")
					return
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

// handleAPIGuard: GET lists the blocks; POST {src, model, reason} adds one
// (and cancels that client's running requests for the model); DELETE
// ?src=&model= lifts it.
func (s *Server) handleAPIGuard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	switch r.Method {
	case http.MethodPost:
		var b loopBlock
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&b); err != nil || b.Src == "" || b.Model == "" {
			swaputil.SendResponse(w, r, http.StatusBadRequest, "want {src, model, reason}")
			return
		}
		b.Since, b.Refused = time.Now(), 0
		s.guard.mu.Lock()
		s.guard.blocks[guardKey(b.Src, b.Model)] = b
		s.guard.mu.Unlock()
		cancelled := 0
		for _, e := range s.inflight.Current().Requests {
			if e.RemoteIP == b.Src && e.Model == b.Model && s.inflight.Cancel(e.ID) {
				cancelled++
			}
		}
		json.NewEncoder(w).Encode(map[string]any{"msg": "ok", "cancelled": cancelled})
	case http.MethodDelete:
		s.guard.mu.Lock()
		delete(s.guard.blocks, guardKey(r.URL.Query().Get("src"), r.URL.Query().Get("model")))
		s.guard.mu.Unlock()
		json.NewEncoder(w).Encode(map[string]string{"msg": "ok"})
	default:
		json.NewEncoder(w).Encode(map[string]any{"blocks": s.guard.list()})
	}
}
