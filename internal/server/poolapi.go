package server

import (
	"encoding/json"
	"net/http"

	"github.com/mostlygeek/llama-swap/internal/router"
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
	json.NewEncoder(w).Encode(reporter.PoolStatus())
}
