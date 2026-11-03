package server

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"
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
	blocks map[string]loopBlock // src + "\x00" + client + "\x00" + model
}

// A block names an address, the app on it (its User-Agent, as clientApp gives
// it; "" for every app there) and a model.
type loopBlock struct {
	Src     string    `json:"src"`
	Client  string    `json:"client"`
	Model   string    `json:"model"`
	Reason  string    `json:"reason"`
	Since   time.Time `json:"since"`
	Refused int       `json:"refused"`
}

func newLoopGuard() *loopGuard { return &loopGuard{blocks: map[string]loopBlock{}} }

func guardKey(src, client, model string) string { return src + "\x00" + client + "\x00" + model }

// clientApp is the app that sent a request, from its User-Agent ("node",
// "OpenAI/Python 2.24.0"): what tells two agents on one machine apart.
func clientApp(r *http.Request) string {
	ua := strings.TrimSpace(r.Header.Get("User-Agent"))
	if len(ua) > 80 {
		ua = ua[:80]
	}
	return ua
}

// blocked reports the block for this request's address, app and model (a
// block for the whole address matches too), counting the refusal.
func (g *loopGuard) blocked(src, client, model string) (loopBlock, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	for _, k := range []string{guardKey(src, client, model), guardKey(src, "", model)} {
		if b, ok := g.blocks[k]; ok {
			b.Refused++
			g.blocks[k] = b
			return b, true
		}
	}
	return loopBlock{}, false
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
				if b, stop := s.guard.blocked(clientIP(r), clientApp(r), data.ModelID); stop {
					w.Header().Set("Retry-After", "60")
					swaputil.SendResponse(w, r, http.StatusTooManyRequests,
						"Argos stopped this app's requests to "+b.Model+": "+b.Reason+
							". It looks like it is repeating one step. Let it run again on Argos -> Schedule -> Live -> Loop guard.")
					return
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

// handleAPIGuard: GET lists the blocks; POST {src, client, model, reason} adds
// one (and cancels that app's running requests for the model; no client: every
// app on the address); DELETE ?src=&client=&model= lifts it.
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
		s.guard.blocks[guardKey(b.Src, b.Client, b.Model)] = b
		s.guard.mu.Unlock()
		cancelled := 0
		for _, e := range s.inflight.Current().Requests {
			app := strings.TrimSpace(e.ReqHeaders["User-Agent"])
			if len(app) > 80 {
				app = app[:80]
			}
			if e.RemoteIP == b.Src && e.Model == b.Model && (b.Client == "" || app == b.Client) && s.inflight.Cancel(e.ID) {
				cancelled++
			}
		}
		json.NewEncoder(w).Encode(map[string]any{"msg": "ok", "cancelled": cancelled})
	case http.MethodDelete:
		s.guard.mu.Lock()
		q := r.URL.Query()
		delete(s.guard.blocks, guardKey(q.Get("src"), q.Get("client"), q.Get("model")))
		s.guard.mu.Unlock()
		json.NewEncoder(w).Encode(map[string]string{"msg": "ok"})
	default:
		json.NewEncoder(w).Encode(map[string]any{"blocks": s.guard.list()})
	}
}
