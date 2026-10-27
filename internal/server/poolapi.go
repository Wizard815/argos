package server

import (
	"encoding/json"
	"net/http"

	"github.com/Wizard815/Argos/internal/router"
)

// handleAPIPool reports the pool router's live state: per-card use, where each
// model runs, and what is queued or loading. Other routers report only that the
// pool is not in use.
func (s *Server) handleAPIPool(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	reporter, ok := s.local.(router.PoolReporter)
	if !ok {
		json.NewEncoder(w).Encode(map[string]any{"router": s.cfg.Routing.Router.Use, "enabled": false})
		return
	}
	json.NewEncoder(w).Encode(s.poolStatus(reporter))
}

// poolStatus is the router's view with the requests in flight counted by the
// server too. The router answers through its run loop and gives up after two
// seconds while that loop is busy (a model loading), reporting nothing in
// flight; the server's own tracker always knows the requests it is serving.
func (s *Server) poolStatus(reporter router.PoolReporter) router.PoolStatus {
	st := reporter.PoolStatus()
	counts := map[string]int{}
	for _, r := range s.inflight.Current().Requests {
		counts[r.Model]++
	}
	for i := range st.Models {
		if n := counts[st.Models[i].ID]; n > st.Models[i].InFlight {
			st.Models[i].InFlight = n
		}
	}
	return st
}

// handleAPIClearCrashes forgets a model's crashes, so requests start it again:
// Schedule -> Live calls it before loading a model by hand.
func (s *Server) handleAPIClearCrashes(w http.ResponseWriter, r *http.Request) {
	router.Crashes.Clear(r.PathValue("model"))
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"msg": "ok"})
}
